"""Entry point for the local WA Bot web application."""

import argparse
import json
import socket
import threading
import time
import webbrowser

import uvicorn

from bot_lock import owner_description, run_lock
from database import Database
from logger import get_logger, setup_logging
from migration import migrate_legacy_data
from paths import RUNTIME_FILE, ensure_data_dirs
from service import BotService, ConfigurationError
from webapp import create_app

log = get_logger(__name__)
DEFAULT_PORT = 8765


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Локальная панель WA Bot")
    parser.add_argument("--once", action="store_true", help="выполнить один прогон")
    parser.add_argument("--no-browser", action="store_true", help="не открывать панель")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="порт панели")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    ensure_data_dirs()
    setup_logging()

    with run_lock() as acquired:
        if not acquired:
            url = _running_url()
            log.info("WA Bot уже работает%s", _owner_suffix())
            if not args.no_browser:
                webbrowser.open(url)
            print(f"Панель уже запущена: {url}")
            return 0

        database = Database()
        database.initialize()
        migrate_legacy_data(database)
        if args.once:
            return _run_once(database)
        return _serve(database, args.port, args.no_browser)


def _serve(database: Database, requested_port: int, no_browser: bool) -> int:
    port = _available_port(requested_port)
    url = f"http://127.0.0.1:{port}"
    _write_runtime(port)
    try:
        if not no_browser:
            threading.Timer(1.0, webbrowser.open, args=(url,)).start()
        print(f"WA Bot запущен: {url}")
        print("Закройте эту вкладку терминала или нажмите Ctrl+C для остановки.")
        uvicorn.run(
            create_app(database=database),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
        return 0
    finally:
        RUNTIME_FILE.unlink(missing_ok=True)


def _run_once(database: Database) -> int:
    service = BotService(database)
    try:
        run_id = service.start_run("manual")
    except ConfigurationError as error:
        log.error("Прогон не запущен: %s", error)
        return 1
    print(f"Запущен прогон #{run_id}")
    try:
        while service.status()["running"]:
            time.sleep(0.5)
    except KeyboardInterrupt:
        service.stop()
        return 130
    run, _ = database.get_run(run_id)
    return 0 if run and run["status"] == "completed" else 1


def _available_port(start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket() as candidate:
            try:
                candidate.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("Не найден свободный локальный порт для веб-панели")


def _write_runtime(port: int) -> None:
    payload = json.dumps({"port": port}, ensure_ascii=False)
    temporary = RUNTIME_FILE.with_suffix(".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(RUNTIME_FILE)


def _running_url() -> str:
    try:
        data = json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
        return f"http://127.0.0.1:{int(data['port'])}"
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return f"http://127.0.0.1:{DEFAULT_PORT}"


def _owner_suffix() -> str:
    owner = owner_description()
    return f" ({owner})" if owner else ""


if __name__ == "__main__":
    raise SystemExit(main())
