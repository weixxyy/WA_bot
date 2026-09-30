import json
import time
from datetime import datetime
from pathlib import Path

from logger import LOG_DIR, get_logger

log = get_logger(__name__)

# Статистика по профилю лежит рядом с его Firefox-профилем.
STATS_FILE_NAME = "bot_stats.json"

# Сводка по прогонам (сколько групп «потерял» каждый профиль) лежит рядом
# с логами, чтобы не смешиваться с Firefox-профилем.
SUMMARY_FILE = LOG_DIR / "stats_summary.json"

# Столько последних прогонов храним в сводке, чтобы файл не рос бесконечно.
MAX_RUNS_IN_SUMMARY = 100


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


def do_script(urls_list, context, profile, message):
        # Профиль нужен, чтобы вести статистику рядом с ним: context его не отдаёт.
        stats = _reset_stats(profile)

        for invite_url in urls_list:
            page = context.new_page()

            for p in context.pages[:-1]:
                p.close()

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
            search2 = page.get_by_test_id("confirm-popup").filter(
                visible=True,
                has_text="Вы не можете вступить в данную группу, так как вы были удалены."
            ).first
            search3 = page.get_by_test_id(
                "conversation-info-header-chat-title"
            )
            search.or_(search2).or_(search3).wait_for(timeout=None)
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
                continue
            elif search.is_visible():
                log.info("Бот вступил в группу %s", group_name)
                search.click()
            else:
                log.info("Бот уже находится в группе %s", group_name)

            message_container = page.locator('[contenteditable="true"]')
            message_container.wait_for()
            # Enter в WhatsApp отправляет сообщение, а перенос строки внутри текста
            # делает Shift+Enter. fill() для многострочного текста не годится: в
            # Firefox он записывает "\n" как символ, который в поле ввода
            # схлопывается в пробел, — поэтому строки набираем по очереди.
            lines = message.split("\n")
            message_container.fill(lines[0])
            for line in lines[1:]:
                message_container.press("Shift+Enter")
                if line:
                    # insert_text("") падает в Firefox (NS_ERROR_FAILURE), а
                    # пустая строка — это просто ещё один перенос.
                    page.keyboard.insert_text(line)
            message_container.press("Enter")

            log.info(
                "Сообщение отправлено в группу %s (%s символов)",
                group_name,
                len(message),
            )

            log.info("-- Следующий чат --")

            time.sleep(3)