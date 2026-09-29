"""Межпроцессная блокировка запуска бота: ``logs/wa_bot.lock``.

Два прогона по одним и тем же каталогам профилей ломают Firefox-профиль,
поэтому на время работы процесс держит эксклюзивную блокировку файла
средствами ОС: ``fcntl.flock`` на POSIX и ``msvcrt.locking`` на Windows.

Занятость определяем именно попыткой захватить блокировку, а не PID из
содержимого файла: ОС снимает блокировку сама, когда процесс завершился
(в том числе аварийно), поэтому «протухших» блокировок не бывает. Содержимое
файла (PID и время запуска) нужно только для понятного сообщения человеку.

Пример использования::

    from bot_lock import owner_description, run_lock

    with run_lock() as acquired:
        if not acquired:
            print(f"Бот уже работает ({owner_description()})")
        else:
            ...
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - только Windows
    fcntl = None

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - только POSIX
    msvcrt = None

from logger import get_logger
from paths import LOCK_FILE

log = get_logger(__name__)

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# msvcrt.locking блокирует байты, поэтому в пустом файле сначала создаём байт.
LOCK_BYTES = 1


def _try_lock(descriptor: int) -> bool:
    """Пробует взять эксклюзивную блокировку, не дожидаясь освобождения.

    :return: ``True`` — блокировка взята, ``False`` — файл занят другим процессом.
    """
    if fcntl is not None:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # EWOULDBLOCK / EAGAIN — файл уже кем-то заблокирован.
            return False
        return True

    if msvcrt is not None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, LOCK_BYTES)
        except OSError:
            return False
        return True

    raise RuntimeError("Не нашлось механизма блокировки файлов для этой платформы")


def _unlock(descriptor: int) -> None:
    """Снимает блокировку, взятую :func:`_try_lock`."""
    if fcntl is not None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return

    if msvcrt is not None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        with suppress(OSError):  # pragma: no cover - снимать уже нечего
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, LOCK_BYTES)


def _open_lock_file(lock_file) -> int:
    """Открывает лок-файл (создавая его при необходимости) и отдаёт дескриптор."""
    lock_file = Path(lock_file)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    return os.open(lock_file, os.O_RDWR | os.O_CREAT)


def _ensure_one_byte(descriptor: int) -> None:
    """Гарантирует, что в файле есть байт, который можно заблокировать."""
    if os.fstat(descriptor).st_size == 0:
        os.write(descriptor, b" ")


def _write_owner(descriptor: int) -> None:
    """Пишет в файл, кто держит блокировку: нужно только для сообщений."""
    owner = f"PID {os.getpid()}, запущен {datetime.now().strftime(TIME_FORMAT)}"
    payload = owner.encode("utf-8")
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.write(descriptor, payload)
    os.ftruncate(descriptor, len(payload))


def owner_description(lock_file=LOCK_FILE) -> str | None:
    """Возвращает содержимое лок-файла для сообщения или ``None``.

    Строка может остаться от прошлого запуска, поэтому судить по ней о том,
    работает ли бот, нельзя: для этого есть :func:`is_locked`.
    """
    try:
        return Path(lock_file).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def is_locked(lock_file=LOCK_FILE) -> bool:
    """``True``, если бот работает прямо сейчас."""
    descriptor = _open_lock_file(lock_file)
    try:
        _ensure_one_byte(descriptor)
        if not _try_lock(descriptor):
            return True
        _unlock(descriptor)
        return False
    finally:
        os.close(descriptor)


@contextmanager
def run_lock(lock_file=LOCK_FILE) -> Iterator[bool]:
    """Держит блокировку на время работы бота.

    :return: ``True`` — блокировка взята, можно работать;
        ``False`` — бот уже запущен в другом процессе.
    """
    descriptor = _open_lock_file(lock_file)
    acquired = False
    try:
        _ensure_one_byte(descriptor)
        acquired = _try_lock(descriptor)
        if acquired:
            _write_owner(descriptor)
            log.debug("Взята блокировка %s", lock_file)
        else:
            log.debug(
                "Блокировка %s занята: %s", lock_file, owner_description(lock_file)
            )
        yield acquired
    finally:
        if acquired:
            _unlock(descriptor)
        os.close(descriptor)
