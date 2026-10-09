"""Скрипт вступления в группы: только кнопки «Вступить» / «Запрос на вступление».

Отдельный от ``script.py`` сценарий: сообщения он не отправляет, а лишь вступает
в группы по ссылкам из ``urls.txt``. Заодно раскладывает группы по типам в файлы
(см. ``group_lists.py``):

* ``only_admins_groups.txt`` — писать могут только админы;
* ``closed_groups.txt`` — вступление требует одобрения («Запрос на вступление»);
* ``open_groups.txt`` — все остальные.

Группы, из которых профиль был выгнан, не попадают ни в один файл: тип такой
группы определит другой профиль. Ссылки, на которых сценарий упал, тоже никуда
не пишутся — иначе в списки попал бы мусор.

Пометка «нужен запрос на вступление» неизменна: если группа один раз попала в
``closed_groups.txt``, дальше её тип не пересчитывается, даже когда бота в неё
одобрили (см. :func:`_classify`). Раскладывать такую ссылку по другим спискам не
даст :func:`group_lists.is_closed`, а :func:`group_lists.move_to_closed` при
необходимости убирает её из «чужих» файлов.

Заявку мог подать прошлый прогон: пока её не одобрили, WhatsApp показывает на
кнопке «Отменить запрос». Нажимать её нельзя — заявка сбросится, поэтому сценарий
пишет в лог «заявка на вступление уже подана», закрепляет ту же пометку «нужен
запрос» и переходит к следующей ссылке.

Запуск по расписанию — ``python join_groups.py`` (своё расписание,
:data:`scheduler.DEFAULT_JOIN_RUN_AT`), одиночный прогон — ``python
join_groups.py --once``.
"""

import argparse
import time

from playwright.sync_api import sync_playwright

import group_lists
from bot_lock import LOCK_FILE, owner_description, run_lock
from login_check import ensure_profiles_dir, login_check
from logger import get_logger, setup_logging
from scheduler import DEFAULT_JOIN_RUN_AT, parse_run_at, run_forever
from urls_list import URLS_FILE, load_invite_urls

log = get_logger(__name__)

# Сколько ждать после нажатия «Запрос на вступление»: заявка отправлена, но бота
# в группе ещё нет — закрываем лишние вкладки и идём к следующей ссылке.
JOIN_REQUEST_WAIT_SECONDS = 5

# Пауза после вступления/классификации: даём WhatsApp дописать состояние и не
# «долбим» его вкладками подряд.
AFTER_JOIN_WAIT_SECONDS = 2

# Сколько ждать после последней ссылки, прежде чем закрыть окно.
LAST_LINK_WAIT_SECONDS = 5


def _close_extra_pages(context) -> None:
    """Закрывает вкладки контекста, кроме последней; ошибки закрытия не роняют прогон."""
    for page in list(context.pages[:-1]):
        try:
            page.close()
        except Exception:
            log.exception("Не удалось закрыть вкладку")


def _classify(page, group_name, invite_url) -> str:
    """Определяет тип группы, в которой бот уже состоит, и пишет её в файл.

    Тот же признак, что и в ``script.py``: если вместо поля ввода виден блок
    «только админы», группа уходит в ``only_admins_groups.txt``, иначе — в
    ``open_groups.txt``.

    Пометка «нужен запрос на вступление» неизменна: для ссылки из
    ``closed_groups.txt`` тип не пересчитывается (в том числе когда бота в группу
    одобрили), а её запись в «чужих» файлах, если она там была, убирается.

    :return: ``"only_admins"``, ``"open"`` или ``"closed"``.
    """
    if group_lists.is_closed(invite_url):
        if group_lists.move_to_closed(invite_url):
            log.info(
                "Группа %s уже помечена как «нужен запрос» — убрал её из других списков",
                group_name,
            )
        log.info(
            "Пометка группы %s неизменна: тип не пересчитываем",
            group_name,
        )
        return "closed"

    admins_only = page.get_by_text(group_lists.ADMINS_ONLY_TEXT).first
    message_container = page.locator('[contenteditable="true"]')
    message_container.or_(admins_only).wait_for()
    if admins_only.is_visible():
        group_lists.add_only_admins(invite_url)
        log.info("Группа %s: писать могут только админы", group_name)
        return "only_admins"
    group_lists.add_open(invite_url)
    log.info("Группа %s: обычная (сообщения разрешены)", group_name)
    return "open"


def _process_invite(invite_url, context):
    """Обрабатывает одну invite-ссылку: вступление или заявка, без сообщений.

    :raises Exception: любая ошибка Playwright (таймаут загрузки, ненайденный
        элемент, упавшая вкладка) — ловится в :func:`do_join_groups`, чтобы обход
        продолжился со следующей ссылки.
    """
    page = context.new_page()
    page.goto(invite_url, wait_until="domcontentloaded")

    log.info("Ожидаем загрузку WhatsApp по ссылке %s", invite_url)
    group_name = page.locator("h3").inner_text()
    log.info("Имя группы: %s", group_name)

    search = page.get_by_role("link", name="Continue to WhatsApp Web").first
    search.wait_for()
    log.info("Ожидаем переход в группу %s", group_name)
    with context.expect_page() as new_page_info:
        search.click()

    log.info("Переходим по ссылке-приглашению")
    page = new_page_info.value

    search = page.get_by_role(role="button", name="Вступить в группу")
    search_request = page.get_by_role(role="button", name="Запрос на вступление")
    # Заявку отправили раньше, но её ещё не одобрили: WhatsApp показывает на этом
    # же месте «Отменить запрос». Состояние то же — «нужен запрос», просто заявка
    # уже подана.
    search_cancel = page.get_by_role(role="button", name="Отменить запрос")
    search2 = page.get_by_test_id("confirm-popup").filter(
        visible=True,
        has_text="Вы не можете вступить в данную группу, так как вы были удалены.",
    ).first
    search3 = page.get_by_test_id("conversation-info-header-chat-title")
    search.or_(search_request).or_(search2).or_(search3).or_(search_cancel).wait_for(
        timeout=None
    )

    if search2.is_visible():
        # Профиль выгнан из группы: тип неизвестен, ни в один файл не пишем —
        # определит другой профиль.
        log.info("Профиль выгнан из группы %s — в списки не добавляем", group_name)
        return
    if search_cancel.is_visible():
        # Заявка уже подана и ждёт одобрения: нажимать нечего, а «Отменить запрос»
        # сбросила бы заявку. Группа точно из «одобряемых» — закрепляем пометку и
        # идём к следующей ссылке, как после нажатия «Запрос на вступление».
        log.info(
            "Группа %s: заявка на вступление уже подана — кнопку не нажимаем",
            group_name,
        )
        if group_lists.move_to_closed(invite_url):
            log.info(
                "Группа %s закреплена в %s: пометка «нужен запрос» неизменна",
                group_name,
                group_lists.CLOSED_FILE.name,
            )
        else:
            log.info(
                "Группа %s уже помечена как «нужен запрос» — пометка неизменна",
                group_name,
            )
        return
    if search_request.is_visible():
        log.info(
            "Группа %s требует одобрения: нажимаем «Запрос на вступление»",
            group_name,
        )
        search_request.click()
        # Пометка «нужен запрос» неизменна: закрепляем её за ссылкой и убираем
        # возможную старую запись из других списков, чтобы группа не оказалась
        # сразу в двух файлах.
        if group_lists.move_to_closed(invite_url):
            log.info(
                "Группа %s закреплена в %s: пометка «нужен запрос» неизменна",
                group_name,
                group_lists.CLOSED_FILE.name,
            )
        else:
            log.info(
                "Группа %s уже помечена как «нужен запрос» — пометка неизменна",
                group_name,
            )
        time.sleep(JOIN_REQUEST_WAIT_SECONDS)
        return
    if search.is_visible():
        log.info("Вступаем в группу %s", group_name)
        search.click()
    else:
        log.info("Бот уже находится в группе %s", group_name)

    _classify(page, group_name, invite_url)
    time.sleep(AFTER_JOIN_WAIT_SECONDS)


def do_join_groups(urls_list, context, profile) -> None:
    """Обходит invite-ссылки профиля, вступая в группы и не срываясь на ошибке.

    Упавшая ссылка не прерывает обход: ошибка с traceback попадает в лог, а
    следующая ссылка обрабатывается как обычно.

    :param urls_list: список ссылок-приглашений.
    :param context: Playwright-контекст профиля (Firefox persistent context).
    :param profile: каталог профиля — нужен только для понятных логов.
    """
    total = len(urls_list)
    for index, invite_url in enumerate(urls_list, start=1):
        log.info("Ссылка %s из %s: %s", index, total, invite_url)
        try:
            _process_invite(invite_url, context)
        except Exception:
            # Сбой на одной ссылке не должен прерывать обход остальных.
            log.exception(
                "Ссылка %s из %s не обработана (%s), перехожу к следующей",
                index,
                total,
                invite_url,
            )
        finally:
            # Вкладки, оставшиеся после ссылки (в том числе от упавшей),
            # закрываем здесь, кроме последней — её оставляем открытой, иначе
            # Firefox-профиль копит страницы.
            _close_extra_pages(context)

    # Последнюю ссылку обработали вплотную к закрытию окна: даём WhatsApp время
    # дописать состояние, прежде чем контекст закроется.
    if urls_list:
        time.sleep(LAST_LINK_WAIT_SECONDS)

    log.info("Профиль %s: вступление по ссылкам завершено (%s шт.)", profile, total)


def run_once() -> dict:
    """Один прогон скрипта вступления: проверка профилей и вступление по ссылкам.

    Ссылки читаются из ``urls.txt`` в начале прогона. Если ссылок нет, прогон
    пропускается (браузеры не поднимаются). Статистику обычной рассылки
    (``bot_stats.json`` / сводка прогонов) скрипт не трогает, чтобы не затирать
    данные :mod:`main`.

    :return: результат :func:`login_check` со списками ``logged``/``unlogged``.
    """
    profiles_dir = ensure_profiles_dir()
    log.info("Каталог профилей: %s", profiles_dir)
    urls = load_invite_urls()
    log.info("Ссылок в %s: %s", URLS_FILE, len(urls))
    if not urls:
        log.error("В %s нет ни одной ссылки — прогон пропущен.", URLS_FILE)
        return {"logged": [], "unlogged": []}

    with sync_playwright() as pw:
        login_dict = login_check(pw)
        if not login_dict["logged"]:
            log.warning(
                "В %s нет ни одного авторизованного профиля.\n"
                "Положите в него каталог с уже выполненным входом в WhatsApp Web "
                "и запустите скрипт снова.",
                profiles_dir,
            )
        for profile in login_dict["logged"]:
            context = pw.firefox.launch_persistent_context(
                user_data_dir=profile,
                headless=True,
            )
            try:
                log.info("Профиль %s: вступаем в группы", profile)
                do_join_groups(urls_list=urls, context=context, profile=profile)
            except Exception:
                # Сбой на одном профиле не должен прерывать обход остальных.
                log.exception("Профиль %s: сценарий прерван ошибкой", profile)
            finally:
                context.close()

    return login_dict



def parse_args(argv=None):
    """Разбирает аргументы командной строки.

    ``--once`` — один прогон и выход, ``--at ЧЧ:ММ ...`` — свои времена запусков
    вместо :data:`scheduler.DEFAULT_JOIN_RUN_AT`.
    """
    parser = argparse.ArgumentParser(
        description="Бот WhatsApp: вступает по invite-ссылкам в группы.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="выполнить один прогон и выйти (без расписания)",
    )
    parser.add_argument(
        "--at",
        nargs="+",
        metavar="ЧЧ:ММ",
        default=None,
        help=(
            "времена запусков в местном времени, например: --at 10:00 19:00 "
            f"(по умолчанию {' '.join(DEFAULT_JOIN_RUN_AT)})"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "запустить, даже если бот уже работает "
            "(второй экземпляр без этого флага не стартует)"
        ),
    )
    args = parser.parse_args(argv)

    if args.once and args.at:
        parser.error("--once и --at несовместимы: --once отключает расписание")
    if args.at:
        # Проверяем формат сразу: опечатка в --at не должна ждать первого слота.
        try:
            args.at = parse_run_at(args.at)
        except ValueError as error:
            parser.error(str(error))
    return args


def run_bot(args) -> None:
    """Выполняет режим из аргументов: один прогон либо работу по расписанию."""
    if args.once:
        log.info("Режим: один прогон")
        run_once()
        log.info("Скрипт вступления завершил работу")
        return

    log.info("Режим: по расписанию")
    run_forever(run_once, args.at or DEFAULT_JOIN_RUN_AT)


def main(argv=None):
    """Точка входа скрипта вступления.

    Держит ту же блокировку, что и ``main.py`` (:data:`bot_lock.LOCK_FILE`):
    два прогона по одним профилям одновременно ломают Firefox-профиль.
    """
    setup_logging()
    args = parse_args(argv)
    log.info("Запуск скрипта вступления в группы")

    if args.force:
        log.warning("--force: блокировку %s не проверяю", LOCK_FILE)
        run_bot(args)
        return

    with run_lock() as acquired:
        if not acquired:
            owner = owner_description()
            log.error(
                "Бот уже запущен%s. Второй экземпляр не стартует: два прогона по "
                "одним профилям ломают Firefox-профиль. Остановите работающий бот "
                "(Ctrl+C) и повторите либо запустите с --force.",
                f" ({owner})" if owner else "",
            )
            raise SystemExit(1)
        run_bot(args)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        # Остановить планировщик можно через Ctrl+C — это штатный выход.
        log.info("Скрипт вступления остановлен пользователем")

