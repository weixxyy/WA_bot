"""Отсортированные по типу группы списки ссылок.

Скрипт вступления (`join_groups.py`) раскладывает ссылки на три файла рядом с
кодом:

* ``only_admins_groups.txt`` — в группе писать могут только админы;
* ``closed_groups.txt`` — вступление требует одобрения («Запрос на вступление»,
  а после отправки заявки кнопка меняется на «Отменить запрос»);
* ``open_groups.txt`` — все остальные группы.

Файлы держим рядом с кодом, а не относительно текущего каталога запуска — та же
логика, что и у ``urls.txt`` / ``send_to.txt`` в ``urls_list.py``. Ссылки
дописываются без дублей, поэтому повторный прогон по тому же ``urls.txt`` не
размножает записи. Группы, из которых профиль был выгнан, сюда не попадают: их
тип определит другой профиль.

Пометка «нужен запрос на вступление» неизменна: ссылка, однажды попавшая в
``closed_groups.txt``, больше никогда не переезжает в другие списки, а
:func:`move_to_closed` при необходимости убирает её оттуда. Снять пометку можно
только вручную, удалив строку из файла.

Пример использования::

    import group_lists

    group_lists.add_open("https://chat.whatsapp.com/...")
"""

from pathlib import Path

from logger import get_logger

log = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
ONLY_ADMINS_FILE = PROJECT_ROOT / "only_admins_groups.txt"
CLOSED_FILE = PROJECT_ROOT / "closed_groups.txt"
OPEN_FILE = PROJECT_ROOT / "open_groups.txt"

# utf-8-sig: «Блокнот» на Windows сохраняет файл с BOM; для UTF-8 без BOM
# поведение то же.
ENCODING = "utf-8-sig"

# Текст, которым WhatsApp сообщает, что писать в группу могут только админы.
# Тот же текст использует script.py, чтобы пропустить такую группу.
ADMINS_ONLY_TEXT = "Только админы могут отправлять сообщения в данную группу"


def load(path) -> list[str]:
    """Читает список ссылок из файла.

    Пустые строки пропускаются, дубликаты схлопываются с сохранением порядка.
    Отсутствующий или недоступный файл трактуется как пустой список: панель
    должна показываться даже до первого прогона скрипта вступления.
    """
    path = Path(path)
    try:
        lines = path.read_text(encoding=ENCODING).splitlines()
    except FileNotFoundError:
        return []
    except OSError:
        log.exception("Не удалось прочитать %s", path)
        return []

    urls = []
    for line in lines:
        candidate = line.strip()
        if candidate and candidate not in urls:
            urls.append(candidate)
    return urls


def _write(path, urls) -> bool:
    """Перезаписывает файл списка целиком (пустой список — пустой файл).

    Перезапись, а не дозапись: так BOM от ``utf-8-sig`` не «утекает» в середину
    файла, а список остаётся без дублей.

    :return: ``False``, если записать файл не удалось (ошибка попадает в лог,
        прогон не падает).
    """
    path = Path(path)
    text = "\n".join(urls) + "\n" if urls else ""
    try:
        path.write_text(text, encoding=ENCODING)
    except OSError:
        log.exception("Не удалось записать %s", path)
        return False
    return True


def _append_unique(path, url) -> bool:
    """Дописывает ссылку в файл, если её там ещё нет.

    :return: ``True``, если ссылка добавлена; ``False`` — если уже была или
        записать файл не удалось (ошибка попадает в лог, прогон не падает).
    """
    path = Path(path)
    urls = load(path)
    if url in urls:
        log.info("Ссылка уже есть в %s: %s", path.name, url)
        return False

    urls.append(url)
    if not _write(path, urls):
        return False
    log.info("Ссылка добавлена в %s: %s", path.name, url)
    return True


def add_only_admins(url) -> bool:
    """Добавляет ссылку в ``only_admins_groups.txt`` (пишут только админы)."""
    return _append_unique(ONLY_ADMINS_FILE, url)


def add_closed(url) -> bool:
    """Добавляет ссылку в ``closed_groups.txt`` (нужен запрос на вступление)."""
    return _append_unique(CLOSED_FILE, url)


def add_open(url) -> bool:
    """Добавляет ссылку в ``open_groups.txt`` (все остальные группы)."""
    return _append_unique(OPEN_FILE, url)


def contains(path, url) -> bool:
    """Есть ли ссылка в файле списка."""
    return url in load(path)


def is_closed(url) -> bool:
    """Помечена ли ссылка как «нужен запрос на вступление».

    Пометка неизменна: получив ``True``, тип группы пересчитывать нельзя — такую
    ссылку ``join_groups.py`` больше не раскладывает по другим спискам.
    """
    return contains(CLOSED_FILE, url)


def remove(path, url) -> bool:
    """Убирает ссылку из файла списка (порядок остальных строк сохраняется).

    :return: ``True``, если ссылка была и файл перезаписан без неё.
    """
    path = Path(path)
    urls = load(path)
    if url not in urls:
        return False

    urls.remove(url)
    if not _write(path, urls):
        return False
    log.info("Ссылка убрана из %s: %s", path.name, url)
    return True


def move_to_closed(url) -> bool:
    """Закрепляет за ссылкой тип «нужен запрос на вступление» (пометка неизменна).

    Ссылка добавляется в ``closed_groups.txt`` и убирается из
    ``only_admins_groups.txt`` / ``open_groups.txt``: одна и та же группа не
    может быть одновременно «закрытой» и «обычной».

    :return: ``True``, если файлы реально изменились.
    """
    removed_only_admins = remove(ONLY_ADMINS_FILE, url)
    removed_open = remove(OPEN_FILE, url)
    added_closed = add_closed(url)
    return added_closed or removed_only_admins or removed_open
