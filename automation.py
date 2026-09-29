"""Bounded Playwright operations for WhatsApp Web."""

import re
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from logger import get_logger

log = get_logger(__name__)
WHATSAPP_WEB = "https://web.whatsapp.com"
NAVIGATION_TIMEOUT_MS = 60_000
STATE_TIMEOUT_MS = 60_000
IMAGE_BATCH_SIZE = 30
QR_WAIT_TIMEOUT_MS = 15_000


@dataclass(frozen=True)
class AttemptOutcome:
    status: str
    group_name: str | None = None
    detail: str | None = None
    safe_to_retry: bool = False


def redact_invite_url(url: str) -> str:
    """Keep logs useful without copying a full bearer invite link."""
    code = url.rstrip("/").split("/")[-1].split("?", 1)[0]
    if len(code) <= 8:
        return "https://chat.whatsapp.com/<hidden>"
    return f"https://chat.whatsapp.com/{code[:4]}…{code[-4:]}"


def inspect_profile(playwright, profile: Path) -> tuple[str, str | None]:
    """Check a profile with the original rule: no QR means logged in."""
    context = None
    try:
        context = playwright.firefox.launch_persistent_context(
            user_data_dir=profile,
            headless=True,
        )
        page = context.new_page()
        try:
            page.goto(WHATSAPP_WEB)
        except PlaywrightError as error:
            log.exception("Не удалось открыть WhatsApp Web для профиля %s", profile)
            return "error", str(error)

        qr = page.locator('canvas[aria-label="Scan this QR code to link a device!"]')
        try:
            qr.wait_for(timeout=QR_WAIT_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            return "authorized", None
        return "needs_login", "Требуется повторно отсканировать QR-код"
    except PlaywrightError as error:
        log.exception("Не удалось проверить профиль %s", profile)
        return "error", str(error)
    finally:
        if context is not None:
            try:
                context.close()
            except PlaywrightError:
                log.warning("Не удалось закрыть проверочный браузер %s", profile)


def authorize_profile(
    playwright,
    profile: Path,
    stop_event,
    confirmed_event,
) -> tuple[str, str | None]:
    """Open WhatsApp and wait for the panel's manual confirmation button."""
    context = None
    try:
        context = playwright.firefox.launch_persistent_context(
            user_data_dir=profile,
            headless=False,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(
            WHATSAPP_WEB,
            wait_until="domcontentloaded",
            timeout=NAVIGATION_TIMEOUT_MS,
        )
        log.info("WhatsApp Web открыт для профиля %s", profile)
        log.info(
            "После входа подтвердите авторизацию кнопкой в веб-панели; "
            "окно Firefox закроется автоматически"
        )
        while not stop_event.is_set():
            if confirmed_event.wait(timeout=0.25):
                log.info("Авторизация профиля %s подтверждена оператором", profile)
                return "authorized", None
        if stop_event.is_set():
            return "needs_login", "Авторизация отменена при остановке приложения"
        return "needs_login", "Авторизация не подтверждена"
    except PlaywrightError as error:
        log.exception("Не удалось авторизовать профиль %s", profile)
        return "error", str(error)
    finally:
        if context is not None:
            try:
                context.close()
            except PlaywrightError:
                log.warning("Не удалось закрыть окно авторизации %s", profile)


def send_to_group(
    context,
    invite_url: str,
    message_text: str,
    image_paths: list[Path],
    message_mode: str,
    stop_event,
) -> AttemptOutcome:
    """Open one invite and perform one bounded send attempt."""
    page = context.new_page()
    pages_to_close = [page]
    sent_started = False
    group_name = None
    try:
        if stop_event.is_set():
            return AttemptOutcome("cancelled")
        page.goto(
            invite_url,
            wait_until="domcontentloaded",
            timeout=NAVIGATION_TIMEOUT_MS,
        )
        log.info("Ожидаем загрузку WhatsApp по ссылке %s", redact_invite_url(invite_url))
        heading = page.locator("h3")
        try:
            group_name = heading.inner_text().strip() or None
        except PlaywrightTimeoutError:
            log.warning("Не удалось прочитать имя группы со страницы приглашения")
        log.info("Имя группы: %s", group_name or "не определено")
        log.info("Ожидаем переход в группу %s", group_name or "не определено")

        page = _continue_to_web(context, page)
        if all(page is not candidate for candidate in pages_to_close):
            pages_to_close.append(page)
        log.info("Переходим по ссылке-приглашению")
        state, target = _wait_for_group_state(page, stop_event)
        if state != "ready":
            if state != "kicked":
                log.warning(
                    "Группа %s пропущена (%s): %s",
                    group_name or "не определено",
                    state,
                    _state_detail(state),
                )
            return AttemptOutcome(state, group_name, _state_detail(state))

        join_button = target
        if join_button is not None:
            log.info("Бот вступил в группу %s", group_name or "не определено")
            join_button.click(timeout=15_000)
            state, _ = _wait_for_group_state(page, stop_event, after_join=True)
            if state != "ready":
                log.warning(
                    "После вступления группа %s не готова к отправке (%s): %s",
                    group_name or "не определено",
                    state,
                    _state_detail(state),
                )
                return AttemptOutcome(state, group_name, _state_detail(state))
        else:
            log.info("Бот уже находится в группе %s", group_name or "не определено")

        if stop_event.is_set():
            return AttemptOutcome("cancelled", group_name)

        log.info("Отправляем сообщение в группу %s", group_name or "не определено")
        if image_paths and message_mode == "caption":
            sent_started = True
            _send_images(page, image_paths, message_text)
        else:
            if message_text.strip():
                sent_started = True
                _send_text(page, message_text)
            if image_paths:
                sent_started = True
                _send_images(page, image_paths, "")
        log.info("Сообщение отправлено в группу %s", group_name or "не определено")
        log.info("-- Следующий чат --")
        return AttemptOutcome("sent", group_name)
    except PlaywrightTimeoutError as error:
        log.warning(
            "Тайм-аут при обработке группы %s: %s",
            group_name or "не определено",
            error,
        )
        return AttemptOutcome(
            "uncertain" if sent_started else "error",
            group_name,
            f"Истёк тайм-аут: {error}",
            safe_to_retry=not sent_started,
        )
    except PlaywrightError as error:
        log.warning(
            "Ошибка WhatsApp при обработке группы %s: %s",
            group_name or "не определено",
            error,
        )
        return AttemptOutcome(
            "uncertain" if sent_started else "error",
            group_name,
            str(error),
            safe_to_retry=not sent_started,
        )
    finally:
        for candidate in reversed(pages_to_close):
            with suppress(PlaywrightError):
                candidate.close()


def _continue_to_web(context, page):
    if "web.whatsapp.com" in page.url:
        return page
    continue_link = page.locator('a[href*="web.whatsapp.com"]').first.or_(
        page.get_by_role(
            "link",
            name=re.compile(r"(continue.*whatsapp web|перейти.*whatsapp web)", re.I),
        ).first
    )
    continue_link.wait_for(timeout=20_000)
    existing_pages = set(context.pages)
    continue_link.click(timeout=15_000)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        new_pages = [candidate for candidate in context.pages if candidate not in existing_pages]
        if new_pages:
            target = new_pages[-1]
            target.wait_for_load_state("domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
            return target
        if "web.whatsapp.com" in page.url:
            return page
        page.wait_for_timeout(250)
    raise PlaywrightTimeoutError("WhatsApp Web не открылся после invite-ссылки")


def _wait_for_group_state(page, stop_event, after_join=False):
    deadline = time.monotonic() + STATE_TIMEOUT_MS / 1000
    join = page.get_by_role(
        "button", name=re.compile(r"(join group|вступить в группу)", re.I)
    ).first
    composer = _composer(page)
    conversation_title = page.get_by_test_id("conversation-info-header-chat-title")
    removed_popup = page.get_by_test_id("confirm-popup").filter(
        visible=True,
        has_text="Вы не можете вступить в данную группу, так как вы были удалены.",
    ).first
    states = [
        ("kicked", removed_popup),
        (
            "kicked",
            page.get_by_text(
                re.compile(r"(removed from.*group|были удалены.*групп)", re.I)
            ).first,
        ),
        (
            "invalid_link",
            page.get_by_text(
                re.compile(r"(invite link.*invalid|ссылка.*недействитель)", re.I)
            ).first,
        ),
        (
            "group_full",
            page.get_by_text(re.compile(r"(group is full|группа заполнена)", re.I)).first,
        ),
        (
            "approval_required",
            page.get_by_text(
                re.compile(r"(request to join|запрос.*вступлен)", re.I)
            ).first,
        ),
        (
            "needs_login",
            page.get_by_text(re.compile(r"(scan.*qr|сканир.*qr)", re.I)).first,
        ),
    ]
    while time.monotonic() < deadline:
        if stop_event.is_set():
            return "cancelled", None
        for status, locator in states:
            if _is_visible(locator):
                return status, locator
        if not after_join and _is_visible(join):
            return "ready", join
        if _is_visible(conversation_title):
            return "ready", None
        if _is_visible(composer):
            return "ready", None
        page.wait_for_timeout(500)
    raise PlaywrightTimeoutError("Не удалось определить состояние группы")


def _composer(page):
    return page.locator('[contenteditable="true"]')


def _send_text(page, text: str) -> None:
    composer = _composer(page)
    composer.wait_for(timeout=20_000)
    composer.fill(text)
    composer.press("Enter")


def _send_images(page, image_paths: list[Path], caption: str) -> None:
    for index in range(0, len(image_paths), IMAGE_BATCH_SIZE):
        batch = image_paths[index : index + IMAGE_BATCH_SIZE]
        _send_image_batch(page, batch, caption if index == 0 else "")


def _send_image_batch(page, image_paths: list[Path], caption: str) -> None:
    attach = page.get_by_role(
        "button", name=re.compile(r"(attach|прикрепить)", re.I)
    ).first.or_(page.locator('[data-testid="clip"]').first)
    if _is_visible(attach):
        attach.click(timeout=10_000)
    file_input = page.locator('input[type="file"][accept*="image"]').last
    file_input.wait_for(state="attached", timeout=15_000)
    file_input.set_input_files([str(path) for path in image_paths])

    dialog = page.locator('[role="dialog"]').last
    dialog.wait_for(timeout=30_000)
    if caption.strip():
        caption_box = dialog.locator('[contenteditable="true"]').last
        caption_box.fill(caption)
    send = dialog.get_by_role(
        "button", name=re.compile(r"(send|отправить)", re.I)
    ).last.or_(dialog.locator('[data-testid="send"]').last)
    send.click(timeout=20_000)


def _is_visible(locator) -> bool:
    try:
        return locator.count() > 0 and locator.is_visible()
    except PlaywrightError:
        return False


def _state_detail(status: str) -> str:
    return {
        "kicked": "Аккаунт был удалён из группы",
        "invalid_link": "Ссылка-приглашение недействительна",
        "group_full": "Группа заполнена",
        "approval_required": "Для вступления требуется одобрение",
        "needs_login": "Требуется авторизация WhatsApp-аккаунта",
        "cancelled": "Прогон остановлен",
    }.get(status, status)
