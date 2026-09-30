"""Источники данных бота: ссылки-приглашения и текст сообщения.

Ссылки лежат в ``urls.txt`` (по одной ссылке в строке), текст сообщения — в
``send_to.txt``. Файлы держим рядом с кодом, а не относительно текущего каталога
запуска — та же логика, что и для ``profiles/`` в login_check.py и ``logs/``
в logger.py.

Оба файла читаются заново перед каждым прогоном, поэтому правки подхватываются
планировщиком без перезапуска бота. В git попадают только шаблоны
``urls.txt.example`` и ``send_to.txt.example``.

Пример использования::

    from urls_list import load_invite_urls, load_message

    urls = load_invite_urls()
    message = load_message()
"""

from pathlib import Path

from logger import get_logger

log = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
URLS_FILE = PROJECT_ROOT / "urls.txt"
MESSAGE_FILE = PROJECT_ROOT / "send_to.txt"

# Текст по умолчанию: используется, пока send_to.txt ещё не создан (раньше это
# значение было зашито прямо в script.py).
DEFAULT_MESSAGE = "ping"

# utf-8-sig: «Блокнот» сохраняет файл с BOM, и без этого первая ссылка получила
# бы невидимый префикс. Для UTF-8 без BOM поведение то же.
ENCODING = "utf-8-sig"

# Строка urls.txt, начинающаяся с этого символа, считается комментарием.
COMMENT_PREFIX = "#"


def load_invite_urls(path=URLS_FILE) -> list[str]:
    """Читает ссылки-приглашения из ``urls.txt`` (по одной ссылке в строке).

    Пустые строки и строки, начинающиеся с ``#``, пропускаются, пробелы по краям
    обрезаются, дубликаты схлопываются с сохранением порядка ссылок.

    :raises FileNotFoundError: если файла нет. Без ссылок бот работать не может,
        поэтому лучше сказать об этом явно, чем молча не сделать ничего.
    """
    path = Path(path)
    try:
        lines = path.read_text(encoding=ENCODING).splitlines()
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"Файл со ссылками {path} не найден. Создайте его рядом с кодом "
            f"(по одной ссылке в строке) или скопируйте {path.name}.example."
        ) from error

    urls = []
    skipped = 0
    for line in lines:
        candidate = line.strip()
        if not candidate or candidate.startswith(COMMENT_PREFIX) or candidate in urls:
            skipped += 1
            continue
        urls.append(candidate)

    log.info(
        "Из %s прочитано ссылок: %s (пропущено пустых строк, комментариев и "
        "дублей: %s)",
        path,
        len(urls),
        skipped,
    )
    return urls


def load_message(path=MESSAGE_FILE) -> str:
    """Читает текст сообщения из ``send_to.txt``.

    Содержимое берётся целиком: переносы строк внутри файла сохраняются, поэтому
    многострочный текст уходит в группу одним многострочным сообщением. Пробелы и
    пустые строки по краям обрезаются, чтобы случайный перевод строки в конце
    файла не удлинял сообщение.

    :return: текст сообщения; :data:`DEFAULT_MESSAGE`, если файла ещё нет или он
        пуст, — отсутствие конфига не должно останавливать бота.
    """
    path = Path(path)
    try:
        message = path.read_text(encoding=ENCODING)
    except FileNotFoundError:
        log.warning(
            "Файл с текстом сообщения %s не найден, отправляю %r. Создайте %s, "
            "чтобы задать свой текст.",
            path,
            DEFAULT_MESSAGE,
            path.name,
        )
        return DEFAULT_MESSAGE

    message = message.strip()
    if not message:
        log.warning("%s пуст, отправляю %r", path, DEFAULT_MESSAGE)
        return DEFAULT_MESSAGE

    log.info("Из %s прочитано сообщение: %s символов", path, len(message))
    return message