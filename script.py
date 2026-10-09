import json
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import group_lists
from logger import LOG_DIR, get_logger

log = get_logger(__name__)

# Статистика по профилю лежит рядом с его Firefox-профилем.
STATS_FILE_NAME = "bot_stats.json"

# Сводка по прогонам (сколько групп «потерял» каждый профиль) лежит рядом
# с логами, чтобы не смешиваться с Firefox-профилем.
SUMMARY_FILE = LOG_DIR / "stats_summary.json"

# Столько последних прогонов храним в сводке, чтобы файл не рос бесконечно.
MAX_RUNS_IN_SUMMARY = 100

# Сколько ждать после нажатия «Запрос на вступление»: запрос отправлен, но бот в
# группе ещё не состоит — сообщение отправлять некуда, ждём и идём к следующей
# ссылке.
JOIN_REQUEST_WAIT_SECONDS = 5

# Сколько ждать после последней ссылки, прежде чем закрыть окно: даём WhatsApp
# дописать состояние, чтобы закрытие не оборвало отправку сообщения или запрос.
LAST_LINK_WAIT_SECONDS = 5


def _stats_path(profile) -> Path:
    """Путь к файлу статистики внутри каталога профиля."""
    return Path(profile) / STATS_FILE_NAME


def _save_stats(profile, stats) -> None:
    """Пишет статистику профиля в bot_stats.json (UTF-8, читаемый JSON)."""
    _stats_path(profile).write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _reset_stats(profile) -> dict:
    """Создаёт bot_stats.json, если его нет, и обнуляет счётчики.

    Вызывается один раз перед работой бота с профилем. ``kicked_count`` —
    сколько групп, из которых аккаунт был выгнан, встретилось за текущий
    прогон; ``last_date_change`` — время последней записи в файл.
    """
    stats = {
        "kicked_count": 0,
        "last_date_change": datetime.now().isoformat(timespec="seconds"),
    }
    _save_stats(profile, stats)
    log.info("Статистика профиля %s обнулена: %s", profile, stats)
    return stats


def read_stats(profile) -> dict | None:
    """Читает статистику профиля из ``bot_stats.json``.

    :return: словарь статистики либо ``None``, если файла ещё нет (например,
        сценарий упал до старта) или он повреждён: вывод статистики не должен
        ронять прогон по остальным профилям.
    """
    path = _stats_path(profile)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        log.exception("Не удалось прочитать статистику %s", path)
        return None
    if not isinstance(data, dict):
        log.warning("Статистика %s неожиданного формата: %r", path, data)
        return None
    return data


def log_stats(profile) -> int | None:
    """Пишет в лог, из скольких групп профиль был выгнан за прогон.

    Вызывается после отработки профиля, в том числе когда сценарий прервался
    ошибкой: ``bot_stats.json`` к этому моменту уже лежит на диске.

    :return: ``kicked_count`` из ``bot_stats.json`` либо ``None``, если
        статистику прочитать не удалось.
    """
    stats = read_stats(profile)
    if stats is None:
        log.warning(
            "Профиль %s: статистика недоступна (файл не найден или повреждён)",
            profile,
        )
        return None
    kicked = stats.get("kicked_count")
    log.info("Итог по профилю %s: выгнан из %s групп(ы) за прогон", profile, kicked)
    return kicked


def _read_summary() -> list:
    """Читает историю сводок из :data:`SUMMARY_FILE`.

    Отсутствующий или повреждённый файл трактуется как пустая история: сводка
    начнётся заново, а прогон из-за этого не прерывается.
    """
    try:
        data = json.loads(SUMMARY_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError):
        log.exception("Сводка %s повреждена, начинаю историю заново", SUMMARY_FILE)
        return []
    if not isinstance(data, list):
        log.warning("Сводка %s неожиданного формата, начинаю историю заново", SUMMARY_FILE)
        return []
    return data


def save_run_summary(results: dict) -> dict:
    """Дописывает в :data:`SUMMARY_FILE` сводку по прошедшему прогону.

    В ``profiles`` попадают все профили прогона, а в ``total_kicked`` — сумма
    только по тем, чью статистику удалось прочитать (``None`` не учитывается).

    :param results: ``{имя профиля: kicked_count или None}``.
    :return: записанная запись сводки.
    """
    counted = [count for count in results.values() if count is not None]
    entry = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "total_kicked": sum(counted),
        "profiles": results,
    }

    history = _read_summary()
    history.append(entry)
    # Обрезаем историю, чтобы файл не рос бесконечно.
    history = history[-MAX_RUNS_IN_SUMMARY:]

    SUMMARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        SUMMARY_FILE.write_text(
            json.dumps(history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        log.exception("Не удалось записать сводку %s", SUMMARY_FILE)
    return entry


def _close_extra_pages(context) -> None:
    """Закрывает вкладки контекста, кроме последней; ошибки закрытия не роняют прогон."""
    for page in list(context.pages[:-1]):
        try:
            page.close()
        except Exception:
            log.exception("Не удалось закрыть вкладку")


def _skip_not_approved(invite_url, profile, group_name) -> bool:
    """Проверяет пометку «нужен запрос на вступление» и решает, пропускать ли группу.

    Пометку (``group_lists.CLOSED_FILE``) ставит скрипт вступления
    (``join_groups.py``) либо этот же сценарий, нажав «Запрос на вступление», и
    она неизменна. Если сообщение отправить не удалось, а ссылка помечена,
    причина одна — профиль в группу ещё не одобрили: пишем это в лог и сообщаем
    вызывающему коду, что группу нужно пропустить.

    :return: ``True`` — группа помечена, переходим к следующей ссылке;
        ``False`` — пометки нет, сбой обрабатывается как раньше.
    """
    if not group_lists.is_closed(invite_url):
        return False
    log.info(
        "Профиль %s ещё не одобрили в группе %s — пропускаем группу",
        profile,
        group_name,
    )
    log.info("-- Следующий чат --")
    return True


def _process_invite(invite_url, context, profile, message, stats):
    """Обрабатывает одну invite-ссылку: вход в группу и отправка сообщения.

    :raises Exception: любая ошибка Playwright (таймаут загрузки,
        ненайденный элемент, упавшая вкладка) — ловится в :func:`do_script`,
        чтобы обход продолжился со следующей ссылки.
    """
    page = context.new_page()

    page.goto(
        invite_url,
        wait_until="domcontentloaded",
    )

    log.info("Ожидаем загрузку WhatsApp по ссылке %s", invite_url)

    group_name = page.locator("h3").inner_text()

    log.info("Имя группы: %s", group_name)

    search = page.get_by_role(
        "link",
        name="Continue to WhatsApp Web"
    ).first

    search.wait_for()

    log.info("Ожидаем переход в группу %s", group_name)

    with context.expect_page() as new_page_info:
        search.click()

    log.info("Переходим по ссылке-приглашению")

    page = new_page_info.value

    search = page.get_by_role(
        role="button",
        name='Вступить в группу'
    )
    search_request = page.get_by_role(
        role="button",
        name='Запрос на вступление'
    )
    # Заявку отправили раньше, но её ещё не одобрили: WhatsApp показывает на этом
    # же месте «Отменить запрос». Состояние то же — «нужен запрос», просто заявка
    # уже подана.
    search_cancel = page.get_by_role(
        role="button",
        name='Отменить запрос'
    )
    search2 = page.get_by_test_id("confirm-popup").filter(
        visible=True,
        has_text="Вы не можете вступить в данную группу, так как вы были удалены."
    ).first
    search3 = page.get_by_test_id(
        "conversation-info-header-chat-title"
    )
    search.or_(search_request).or_(search2).or_(search3).or_(search_cancel).wait_for(
        timeout=None
    )
    if search2.is_visible():
        stats["kicked_count"] += 1
        stats["last_date_change"] = datetime.now().isoformat(
            timespec="seconds"
        )
        _save_stats(profile, stats)
        log.info(
            "Бот был удален из группы %s (kicked_count=%s)",
            group_name,
            stats["kicked_count"],
        )
        return
    elif search_cancel.is_visible():
        # Заявка уже подана и ждёт одобрения: нажимать нечего, а «Отменить запрос»
        # сбросила бы заявку. Группа точно из «одобряемых» — закрепляем ту же
        # пометку, что и после отправки заявки, и идём к следующей ссылке.
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
        log.info("-- Следующий чат --")
        return
    elif search_request.is_visible():
        # Кнопка заявки на месте — сообщение отправить нельзя. Если группа
        # помечена как «нужен запрос», профиль просто ещё не одобрили: пишем это
        # в лог и переходим к следующей ссылке, ничего не нажимая.
        if _skip_not_approved(invite_url, profile, group_name):
            return
        log.info(
            "Группа %s требует одобрения: нажимаем «Запрос на вступление»",
            group_name,
        )
        search_request.click()
        # Пометка «нужен запрос» неизменна: закрепляем её за ссылкой, чтобы
        # следующие прогоны сами пропускали такую группу как «ещё не одобренную»
        # и она не оказалась сразу в двух списках.
        if group_lists.move_to_closed(invite_url):
            log.info(
                "Группа %s закреплена в %s: пометка «нужен запрос» неизменна",
                group_name,
                group_lists.CLOSED_FILE.name,
            )
        # Запрос отправлен, но бота в группе ещё нет — сообщение отправлять
        # некуда. Ждём и переходим к следующей ссылке.
        time.sleep(JOIN_REQUEST_WAIT_SECONDS)
        log.info("-- Следующий чат --")
        return
    elif search.is_visible():
        log.info("Бот вступил в группу %s", group_name)
        search.click()
    else:
        log.info("Бот уже находится в группе %s", group_name)

    # В группе «только для админов» поля ввода нет: вместо него WhatsApp
    # показывает блок с текстом group_lists.ADMINS_ONLY_TEXT. Ждём любой из двух
    # и, если это блок админов, просто пропускаем группу — сообщение отправлять
    # некуда. Список таких групп заполняет скрипт вступления (join_groups.py).
    admins_only = page.get_by_text(group_lists.ADMINS_ONLY_TEXT).first
    message_container = page.locator('[contenteditable="true"]')
    try:
        message_container.or_(admins_only).wait_for()
    except PlaywrightTimeoutError:
        # Поля ввода нет — отправлять сообщение некуда. Для помеченной группы это
        # значит, что заявку ещё не одобрили: пропускаем её и идём к следующей.
        if _skip_not_approved(invite_url, profile, group_name):
            return
        raise
    if admins_only.is_visible():
        log.info(
            "В группе %s писать могут только админы — сообщение не отправляем",
            group_name,
        )
        log.info("-- Следующий чат --")
        return

    # Enter в WhatsApp отправляет сообщение, а перенос строки внутри текста
    # делает Shift+Enter. fill() для многострочного текста не годится: в
    # Firefox он записывает "\n" как символ, который в поле ввода
    # схлопывается в пробел, — поэтому строки набираем по очереди.
    lines = message.split("\n")
    try:
        message_container.fill(lines[0])
        for line in lines[1:]:
            message_container.press("Shift+Enter")
            if line:
                # insert_text("") падает в Firefox (NS_ERROR_FAILURE), а
                # пустая строка — это просто ещё один перенос.
                page.keyboard.insert_text(line)
        message_container.press("Enter")
    except Exception as error:
        # Отправка сорвалась. У помеченной группы причина та же — заявку ещё не
        # одобрили: пропускаем её, остальные сбои обрабатываются как раньше.
        log.info("Не удалось отправить сообщение в группу %s: %s", group_name, error)
        if _skip_not_approved(invite_url, profile, group_name):
            return
        raise

    log.info(
        "Сообщение отправлено в группу %s (%s символов)",
        group_name,
        len(message),
    )

    log.info("-- Следующий чат --")

    time.sleep(3)


def do_script(urls_list, context, profile, message):
    """Обходит invite-ссылки профиля, не прерываясь на ошибке одной из них.

    Ссылки обрабатываются по очереди. Если на одной ссылке сценарий упал
    (не нашлась кнопка входа, таймаут загрузки, падение вкладки, сеть), ошибка
    с traceback попадает в лог, а обход продолжается со следующей ссылки:
    из-за одной проблемной группы не теряются все остальные.
    """
    # Профиль нужен, чтобы вести статистику рядом с ним: context его не отдаёт.
    stats = _reset_stats(profile)

    total = len(urls_list)
    for index, invite_url in enumerate(urls_list, start=1):
        log.info("Ссылка %s из %s: %s", index, total, invite_url)
        try:
            _process_invite(invite_url, context, profile, message, stats)
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
            # закрываем здесь, кроме последней — её оставляем открытой,
            # иначе Firefox-профиль копит страницы.
            _close_extra_pages(context)

    # Последнюю ссылку обработали вплотную к закрытию окна: даём WhatsApp время
    # дописать состояние, прежде чем main.py закроет контекст.
    if urls_list:
        time.sleep(LAST_LINK_WAIT_SECONDS)

    log.info("Профиль %s: обход ссылок завершён (%s шт.)", profile, total)