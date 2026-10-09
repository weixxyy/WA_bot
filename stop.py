"""Останавливает панель WA_bot или прогон бота, даже если окно терминала закрыто.

Панель (``webapp.py``) и прогоны (``main.py``, ``join_groups.py``) держат
``logs/wa_bot.lock`` и штатно завершаются по ``Ctrl+C`` в своём окне терминала.
Если окно закрылось, а процесс остался (запуск через ``nohup``, ``&``, из IDE или
из другого сеанса), останавливать становится нечем. Этот скрипт находит процесс по
служебным файлам и гасит его штатным сигналом:

* ``logs/wa_panel.json`` — PID и порт работающей панели;
* ``logs/wa_bot.lock`` — кто держит блокировку бота (содержимое файла остаётся и
  после завершения процесса, поэтому «живость» PID проверяется отдельно).

Перед остановкой командная строка процесса сверяется с точками входа проекта
(:data:`PROCESS_MARKERS`): чужой процесс с тем же номером PID не трогаем.

Запуск (обёртки — ``./stop.sh``, ``stop.bat``, ``stop.command``)::

    .venv/bin/python stop.py              # остановить панель или прогон
    .venv/bin/python stop.py --dry-run    # только показать цель
    .venv/bin/python stop.py --force      # убить, не дожидаясь штатного выхода
"""

import argparse
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

from bot_lock import LOCK_FILE
from logger import LOG_DIR, get_logger, setup_logging

log = get_logger(__name__)

RUNTIME_FILE = LOG_DIR / "wa_panel.json"

# Точки входа проекта: по ним отличаем свой процесс от чужого, которому номер PID
# мог достаться после переиспользования.
PROCESS_MARKERS = ("webapp.py", "main.py", "join_groups.py")

# В lock-файле лежит строка вида «PID 22124, запущен 2026-10-09 03:06:24».
PID_PATTERN = re.compile(r"PID\s+(\d+)")

# Сколько ждать штатного завершения после сигнала, прежде чем сказать человеку
# «не вышло, повторите с --force»; после --force ждём меньше.
TERMINATE_TIMEOUT_SECONDS = 10
KILL_TIMEOUT_SECONDS = 5
POLL_INTERVAL_SECONDS = 0.2

# Linux: командную строку читаем из /proc. Windows: wmic / PowerShell, причём с
# таймаутом — иначе зависший запрос подвесит и сам скрипт остановки.
PROC_DIR = Path("/proc")
COMMAND_TIMEOUT_SECONDS = 10


def _read_runtime(runtime_file) -> tuple[int, int | None] | None:
    """Читает ``wa_panel.json`` и отдаёт ``(PID, порт)`` панели или ``None``."""
    try:
        data = json.loads(Path(runtime_file).read_text(encoding="utf-8"))
        return int(data["pid"]), int(data.get("port"))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _read_lock_owner(lock_file) -> tuple[int, str] | None:
    """Достаёт из lock-файла ``(PID, описание владельца)`` или ``None``.

    Содержимое файла живёт и после завершения процесса, поэтому по нему нельзя
    судить, работает ли бот: пригодность PID проверяет :func:`stop`.
    """
    try:
        text = Path(lock_file).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    match = PID_PATTERN.search(text)
    if match is None:
        return None
    return int(match.group(1)), text


def _run(command: list[str]) -> str:
    """Выполняет служебную команду и отдаёт её вывод (пустая строка при сбое)."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout or "").strip()


def _windows_cmdline(pid: int) -> str | None:
    """Командная строка процесса в Windows: ``wmic``, затем PowerShell.

    В Windows 11 ``wmic`` может быть не установлен, поэтому есть второй вариант.
    Обе команды отдают командную строку целиком — по ней и решаем, наш это
    процесс или чужой. Если не вышло ни то, ни другое, честно возвращаем
    ``None``: лучше отказаться от остановки, чем убить случайный процесс.
    """
    for command in (
        ["wmic", "process", "where", f"processid={pid}", "get", "commandline", "/format:list"],
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
        ],
    ):
        text = _run(command)
        if text:
            return text
    return None


def _cmdline(pid: int) -> str | None:
    """Командная строка процесса; ``None`` — прочитать не удалось."""
    if os.name == "nt":
        return _windows_cmdline(pid)
    try:
        raw = (PROC_DIR / str(pid) / "cmdline").read_bytes()
    except OSError:
        return None
    # Аргументы в /proc разделены нулевыми байтами.
    return raw.replace(b"\\0", b" ").decode("utf-8", "replace").strip() or None


def _is_alive(pid: int) -> bool:
    """``True``, если процесс с таким PID существует."""
    if os.name == "nt":
        # os.kill в Windows завершает процесс вместо проверки, поэтому смотрим
        # список задач.
        return str(pid) in _run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"])
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Процесс есть, но он чужой: сигнал послать не сможем.
        return True
    return True


def _is_ours(cmdline: str | None) -> bool:
    """``True``, если командная строка похожа на точку входа проекта."""
    if not cmdline:
        return False
    return any(marker in cmdline for marker in PROCESS_MARKERS)


def _describe(cmdline: str, port: int | None) -> str:
    """Понятное человеку название процесса для логов."""
    if "webapp.py" in cmdline:
        return f"панель (порт {port})" if port else "панель"
    if "join_groups.py" in cmdline:
        return "прогон вступления в группы (join_groups.py)"
    if "main.py" in cmdline:
        return "прогон рассылки (main.py)"
    return "процесс WA_bot"


def _terminate(pid: int, force: bool) -> None:
    """Просит процесс завершиться; ``force`` — не просит, а убивает."""
    if os.name == "nt":
        command = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            command.append("/F")
        _run(command)
        return
    os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)


def _wait_gone(pid: int, timeout: float) -> bool:
    """Ждёт завершения процесса не дольше ``timeout`` секунд."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_alive(pid):
            return True
        time.sleep(POLL_INTERVAL_SECONDS)
    return not _is_alive(pid)


def _drop_runtime(runtime_file, pid: int) -> None:
    """Убирает ``wa_panel.json``, если он описывает остановленный или мёртвый процесс.

    Панель удаляет этот файл сама, но при выходе по сигналу (uvicorn после
    ``SIGTERM`` перевыставляет сигнал) до этой строки дело не доходит, и файл
    остаётся описывать уже несуществующий процесс. Живой процесс другого PID —
    не трогаем.
    """
    owner = _read_runtime(runtime_file)
    if owner is None:
        return
    if owner[0] != pid and _is_alive(owner[0]):
        return
    try:
        Path(runtime_file).unlink()
        log.info("Убран устаревший %s", runtime_file)
    except OSError:
        log.warning("Не удалось удалить %s — уберите его вручную", runtime_file)


def stop(
    runtime_file=RUNTIME_FILE,
    lock_file=LOCK_FILE,
    force: bool = False,
    dry_run: bool = False,
) -> int:
    """Останавливает панель или прогон бота.

    Цель ищем в двух местах: сначала ``wa_panel.json`` (там PID и порт панели),
    затем lock-файл — так же останавливаются прогоны ``main.py`` и
    ``join_groups.py``. Живость PID проверяем отдельно от содержимого файлов, а
    перед остановкой сверяем командную строку процесса с точками входа проекта:
    чужой процесс с переиспользованным номером PID не трогаем.

    :param runtime_file: файл с PID и портом панели.
    :param lock_file: файл блокировки бота.
    :param force: не просить завершиться, а убивать сразу.
    :param dry_run: только показать цель: ни сигналов, ни удаления файлов.
    :return: код возврата скрипта: 0 — остановлено или останавливать нечего,
        1 — не удалось (в том числе когда PID оказался чужим).
    """
    runtime = _read_runtime(runtime_file)
    owner = _read_lock_owner(lock_file)

    if runtime is not None and _is_alive(runtime[0]):
        pid, port = runtime
    elif owner is not None and _is_alive(owner[0]):
        pid, port = owner[0], None
    else:
        # Служебные файлы переживают процесс: содержимое lock-файла не пропадает,
        # а wa_panel.json остаётся после выхода панели по сигналу.
        stale = runtime[0] if runtime is not None else (owner[0] if owner else None)
        if stale is None:
            log.info("Ни панель, ни бот не запущены — останавливать нечего.")
            return 0
        log.info("PID %s уже завершён — останавливать нечего.", stale)
        if dry_run:
            return 0
        _drop_runtime(runtime_file, stale)
        return 0

    cmdline = _cmdline(pid)
    if not _is_ours(cmdline):
        log.error(
            "PID %s — не процесс WA_bot (%s). Не трогаю: остановите его сами.",
            pid,
            cmdline or "командную строку прочитать не удалось",
        )
        return 1

    what = _describe(cmdline, port)
    if dry_run:
        log.info("--dry-run: остановил бы %s, PID %s.", what, pid)
        return 0

    log.info("Останавливаю: %s, PID %s.", what, pid)
    _terminate(pid, force=force)
    if not _wait_gone(pid, TERMINATE_TIMEOUT_SECONDS):
        if not force:
            log.error(
                "PID %s не завершился за %s с. Если процесс завис, повторите с --force.",
                pid,
                TERMINATE_TIMEOUT_SECONDS,
            )
            return 1
        if not _wait_gone(pid, KILL_TIMEOUT_SECONDS):
            log.error("PID %s не удалось завершить даже с --force.", pid)
            return 1

    _drop_runtime(runtime_file, pid)
    log.info("Остановлено: %s, PID %s. Блокировка %s освобождена.", what, pid, lock_file)
    return 0


def parse_args(argv=None) -> argparse.Namespace:
    """Разбирает аргументы командной строки.

    ``--force`` — убивать, не дожидаясь штатного завершения, ``--dry-run`` —
    только показать, что будет остановлено.
    """
    parser = argparse.ArgumentParser(
        description="Останавливает панель WA_bot или прогон бота.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="не просить процесс завершиться, а убивать сразу (SIGKILL, taskkill /F)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="только показать, что будет остановлено",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    """Точка входа скрипта остановки: разбор аргументов и остановка цели."""
    args = parse_args(argv)
    setup_logging()
    log.info("Запуск остановки панели/бота")
    return stop(force=args.force, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
