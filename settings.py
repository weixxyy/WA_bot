"""Настройки веб-панели: ``settings.json`` рядом с кодом.

Панель хранит здесь расписания запусков (рассылка сообщений и вступление в
группы) и переключатели автозапуска. Файл перечитывается планировщиком панели на
каждом цикле, поэтому правки из интерфейса применяются без перезапуска — та же
логика, что и у ``urls.txt`` / ``send_to.txt`` в ``urls_list.py``.

Значения по умолчанию берутся из :data:`scheduler.DEFAULT_RUN_AT` и
:data:`scheduler.DEFAULT_JOIN_RUN_AT`, чтобы поведение панели и CLI
(``run.sh`` / ``main.py`` / ``join_groups.py``) совпадало. Сам файл в git не
попадает: рядом лежит шаблон ``settings.json.example``.
"""

import json
from pathlib import Path

from logger import get_logger
from scheduler import DEFAULT_JOIN_RUN_AT, DEFAULT_RUN_AT

log = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = PROJECT_ROOT / "settings.json"

# utf-8-sig: «Блокнот» на Windows сохраняет файл с BOM; для UTF-8 без BOM
# поведение то же.
ENCODING = "utf-8-sig"


def default_settings() -> dict:
    """Настройки по умолчанию: оба автозапуска включены, времена как в CLI."""
    return {
        "enabled": True,
        "times": list(DEFAULT_RUN_AT),
        "join_enabled": True,
        "join_times": list(DEFAULT_JOIN_RUN_AT),
    }


def load(path=SETTINGS_FILE) -> dict:
    """Читает настройки из файла.

    Отсутствующий или повреждённый файл трактуется как значения по умолчанию:
    панель должна запускаться даже без конфига, а прогон из-за него не падает.
    """
    try:
        data = json.loads(Path(path).read_text(encoding=ENCODING))
    except FileNotFoundError:
        return default_settings()
    except (OSError, json.JSONDecodeError):
        log.exception("Настройки %s повреждены, беру значения по умолчанию", path)
        return default_settings()

    if not isinstance(data, dict):
        log.warning("Настройки %s неожиданного формата, беру значения по умолчанию", path)
        return default_settings()

    settings = default_settings()
    if isinstance(data.get("enabled"), bool):
        settings["enabled"] = data["enabled"]
    times = data.get("times")
    if isinstance(times, list):
        settings["times"] = [str(value) for value in times]
    if isinstance(data.get("join_enabled"), bool):
        settings["join_enabled"] = data["join_enabled"]
    join_times = data.get("join_times")
    if isinstance(join_times, list):
        settings["join_times"] = [str(value) for value in join_times]
    return settings


def save(settings: dict, path=SETTINGS_FILE) -> None:
    """Пишет настройки в файл (UTF-8, читаемый JSON)."""
    path = Path(path)
    path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
