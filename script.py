import json
import os
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from automation import send_to_group
from logger import get_logger
from paths import LOG_DIR

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
    _atomic_write_json(_stats_path(profile), stats)


def _atomic_write_json(path: Path, value) -> None:
    """Write JSON through a sibling temporary file and atomically replace it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


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


def start_profile_run(profile) -> dict:
    """Start the original per-profile statistics from the web wrapper."""
    return _reset_stats(profile)


def record_kicked(profile, stats: dict, group_name: str | None) -> None:
    """Apply the original kicked-group counter and log message."""
    stats["kicked_count"] += 1
    stats["last_date_change"] = datetime.now().isoformat(timespec="seconds")
    _save_stats(profile, stats)
    log.info(
        "Бот был удален из группы %s (kicked_count=%s)",
        group_name or "не определено",
        stats["kicked_count"],
    )


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
        _atomic_write_json(SUMMARY_FILE, history)
    except OSError:
        log.exception("Не удалось записать сводку %s", SUMMARY_FILE)
    return entry


def do_script(urls_list, context, profile):
    """Compatibility wrapper for the pre-web command-line workflow."""
    stats = _reset_stats(profile)
    stop_event = threading.Event()
    for invite_url in urls_list:
        outcome = send_to_group(
            context,
            invite_url,
            "ping",
            [],
            "caption",
            stop_event,
        )
        if outcome.status == "kicked":
            record_kicked(profile, stats, outcome.group_name)
        elif outcome.status != "sent":
            log.warning(
                "Группа пропущена (%s): %s",
                outcome.status,
                outcome.detail or "без подробностей",
            )
