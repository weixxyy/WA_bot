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
LOGIN_TIMEOUT_SECONDS = 300
IMAGE_BATCH_SIZE = 30


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
    """Return an account state using positive authenticated and login markers."""
    context = None
    try:
        context = playwright.firefox.launch_persistent_context(
            user_data_dir=profile,
            headless=True,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(
            WHATSAPP_WEB,
            wait_until="domcontentloaded",
            timeout=NAVIGATION_TIMEOUT_MS,
        )
        state = wait_for_profile_state(page, timeout_ms=30_000)
        if state == "authorized":
            return state, None
        if state == "needs_login":
            return state, "Требуется повторно отсканировать QR-код"
        return "error", "Не удалось определить состояние WhatsApp Web"
    except PlaywrightError as error:
        log.exception("Не удалось проверить профиль %s", profile)
        return "error", str(error)
    finally:
        if context is not None:
            try:
                context.close()
            except PlaywrightError:
                log.warning("Не удалось закрыть проверочный браузер %s", profile)


def authorize_profile(playwright, profile: Path, stop_event) -> tuple[str, str | None]:
    """Open a visible browser and wait until login is positively detected."""
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
        deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
        while time.monotonic() < deadline and not stop_event.is_set():
            state = detect_profile_state(page)
            if state == "authorized":
                return state, None
            page.wait_for_timeout(750)
        if stop_event.is_set():
            return "needs_login", "Авторизация отменена при остановке приложения"
        return "needs_login", "Время ожидания QR-кода истекло"
    except PlaywrightError as error:
        log.exception("Не удалось авторизовать профиль %s", profile)
        return "error", str(error)
    finally:
        if context is not None:
            try:
                context.close()
            except PlaywrightError:
                log.warning("Не удалось закрыть окно авторизации %s", profile)


def wait_for_profile_state(page, timeout_ms: int) -> str:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        state = detect_profile_state(page)
        if state:
            return state
        page.wait_for_timeout(500)
    return "error"


def detect_profile_state(page) -> str | None:
    authenticated = (
        page.locator("#pane-side")
        .or_(page.locator('[data-testid="chat-list"]'))
    )
    if _is_visible(authenticated):
        return "authorized"

    qr = page.locator(
        'canvas[data-ref], canvas[aria-label*="QR" i], canvas[aria-label*="код" i]'
    ).first
    login_text = page.get_by_text(
        re.compile(r"(scan.*qr|сканир.*qr|link with phone|привязать.*телефон)", re.I)
    )
    if _is_visible(qr) or _is_visible(login_text):
        return "needs_login"
    return None


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
        log.info("Открыта ссылка группы %s", redact_invite_url(invite_url))
        heading = page.locator("h3").first
        try:
            heading.wait_for(timeout=10_000)
            group_name = heading.inner_text().strip() or None
        except PlaywrightTimeoutError:
            pass

        page = _continue_to_web(context, page)
        if all(page is not candidate for candidate in pages_to_close):
            pages_to_close.append(page)
        state, target = _wait_for_group_state(page, stop_event)
        if state != "ready":
            return AttemptOutcome(state, group_name, _state_detail(state))

        join_button = target
        if join_button is not None:
            join_button.click(timeout=15_000)
            state, _ = _wait_for_group_state(page, stop_event, after_join=True)
            if state != "ready":
                return AttemptOutcome(state, group_name, _state_detail(state))

        if stop_event.is_set():
            return AttemptOutcome("cancelled", group_name)

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
        return AttemptOutcome("sent", group_name)
    except PlaywrightTimeoutError as error:
        return AttemptOutcome(
            "uncertain" if sent_started else "error",
            group_name,
            f"Истёк тайм-аут: {error}",
            safe_to_retry=not sent_started,
        )
    except PlaywrightError as error:
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
    states = [
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
        if _is_visible(composer):
            return "ready", None
        if not after_join and _is_visible(join):
            return "ready", join
        page.wait_for_timeout(500)
    raise PlaywrightTimeoutError("Не удалось определить состояние группы")


def _composer(page):
    return page.locator('footer [contenteditable="true"][role="textbox"]').last.or_(
        page.locator('footer [contenteditable="true"]').last
    )


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
