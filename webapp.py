"""Локальная веб-панель WA_bot на FastAPI.

Панель слушает только ``127.0.0.1`` и дополнительно проверяет заголовок ``Host``
(``TrustedHostMiddleware``), поэтому доступна лишь с того же компьютера, где
запущен бот. Изменяющие запросы требуют CSRF-токен, встроенный в отданную
страницу: сторонний сайт не может прочитать его из-за политики одного источника.
Все операции вызывают существующие функции бота (``main.run_once``,
``join_groups.run_once``, ``login``, ``login_check``) — логика прогона не меняется.
Кнопка «Остановить панель» (``POST /api/stop``) гасит сам процесс: uvicorn
завершается штатно, lifespan останавливает планировщик, а ``finally`` убирает
``logs/wa_panel.json`` и освобождает блокировку бота.

Запуск::

    python webapp.py [--port 8765] [--no-browser]
"""

import argparse
import json
import os
import re
import secrets
import signal
import socket
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

import group_lists
import urls_list
from bot_lock import owner_description, run_lock
from logger import LOG_DIR, LOG_FILE, get_logger, setup_logging
from service import BotService, ConfigurationError, ServiceBusyError

log = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
PANEL_DIR = PROJECT_ROOT / "panel"
RUNTIME_FILE = LOG_DIR / "wa_panel.json"

DEFAULT_PORT = 8765
# Сколько портов подряд пробовать, если занят основной.
PORT_ATTEMPTS = 20

# Ограничение на размер отдаваемых логов, чтобы не грузить браузер.
MAX_LOG_LINES = 400
DEFAULT_LOG_LINES = 200

# UTF-8 без BOM: файлы правит панель, BOM здесь не нужен.
ENCODING = "utf-8"

# Ссылки-приглашения WhatsApp, которые можно выловить из произвольного текста.
INVITE_PATTERN = re.compile(
    r"https?://chat\.whatsapp\.com/[A-Za-z0-9_-]+(?:\?[^\s<>\"']*)?",
    re.IGNORECASE,
)

# Статика панели: её mtime — «версия страницы» в ссылках на app.css/app.js.
PANEL_ASSETS = (PANEL_DIR / "app.css", PANEL_DIR / "app.js")

# Модули бота и панели: если любой из них новее старта панели, в памяти остался
# старый код (Python читает модули один раз), а значит правки не применяются.
TRACKED_CODE = (
    "group_lists.py",
    "join_groups.py",
    "login.py",
    "login_check.py",
    "main.py",
    "scheduler.py",
    "script.py",
    "service.py",
    "settings.py",
    "urls_list.py",
    "webapp.py",
)

# Момент запуска процесса панели: с ним сравниваются mtime файлов кода.
PANEL_STARTED_AT = time.time()


def _mtime(path) -> float:
    """Время последней правки файла (0.0, если файла нет)."""
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0


def _panel_version() -> str:
    """Версия статики панели для ссылок ``?v=`` — гасит кэш браузера."""
    return str(int(max(_mtime(asset) for asset in PANEL_ASSETS)))


def _code_stale() -> bool:
    """``True``, если файлы кода новее запуска панели (правки не применены)."""
    return any(_mtime(PROJECT_ROOT / name) > PANEL_STARTED_AT for name in TRACKED_CODE)


class TextPayload(BaseModel):
    """Тело запроса для редакторов текста (сообщение, список ссылок)."""

    text: str = ""


class SchedulePayload(BaseModel):
    """Тело запроса для расписания."""

    enabled: bool = True
    times: list[str] = []


class ProfilePayload(BaseModel):
    """Тело запроса для добавления номера."""

    name: str


def _tail(path, lines: int) -> str:
    """Возвращает последние ``lines`` строк файла (или заглушку, если пусто)."""
    try:
        with Path(path).open(encoding=ENCODING, errors="replace") as handle:
            return "".join(handle.readlines()[-lines:])
    except FileNotFoundError:
        return "Лог пока пуст."


def _read_urls_text() -> str:
    """Сырой текст ``urls.txt`` (пустая строка, если файла ещё нет)."""
    try:
        return urls_list.URLS_FILE.read_text(encoding=urls_list.ENCODING)
    except FileNotFoundError:
        return ""


def _parse_urls(text: str) -> list[str]:
    """Ссылки из текста: без комментариев, пробелов и дублей (как в urls_list)."""
    urls = []
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith(urls_list.COMMENT_PREFIX):
            continue
        if candidate not in urls:
            urls.append(candidate)
    return urls


def _extract_invites(text: str) -> list[str]:
    """Вылавливает ссылки-приглашения из произвольного текста (без дублей)."""
    found = []
    for match in INVITE_PATTERN.findall(text):
        if match not in found:
            found.append(match)
    return found


def _write_text_file(path, text: str) -> None:
    """Пишет файл конфигурации, превращая ошибки доступа в понятный 500."""
    try:
        Path(path).write_text(text, encoding=ENCODING)
    except OSError as error:
        log.exception("Не удалось записать %s", path)
        raise HTTPException(status_code=500, detail=f"Не удалось записать {Path(path).name}: {error}")


class PanelStopper:
    """Просьба к uvicorn-серверу завершиться (кнопка панели, закрытие терминала).

    Приложение собирается раньше сервера (``uvicorn.Config`` требует готовый
    ``app``), поэтому ссылку на сервер держим здесь. Все поводы остановки —
    кнопка «Остановить панель» в интерфейсе и ``SIGHUP`` при закрытии окна
    терминала — идут через :meth:`request`: в журнале всегда есть строка с
    причиной, а повторная просьба не гасит панель дважды.

    Панель выключается штатно: uvicorn дописывает текущий ответ, закрывает
    соединения и вызывает lifespan, где :meth:`BotService.stop` останавливает
    планировщик и ждёт текущий прогон до 15 секунд.
    """

    def __init__(self) -> None:
        self._server: uvicorn.Server | None = None
        self.requested = threading.Event()

    def attach(self, server: uvicorn.Server) -> None:
        """Запоминает сервер: до этого момента останавливать ещё нечего."""
        self._server = server

    def request(self) -> bool:
        """Просит сервер завершиться. ``False`` — остановка уже заказана."""
        if self.requested.is_set():
            return False
        if self._server is None:
            log.warning("Остановка панели запрошена до подключения сервера — пропускаю")
            return False
        self.requested.set()
        self._server.should_exit = True
        return True


def create_app(service: BotService, stopper: PanelStopper) -> FastAPI:
    """Собирает FastAPI-приложение панели вокруг готового :class:`BotService`."""
    csrf_token = secrets.token_urlsafe(24)

    @asynccontextmanager
    async def lifespan(_app):
        # Планировщик живёт ровно столько, сколько приложение: на остановке
        # (в т.ч. по SIGHUP при закрытии терминала) он снимает блокировку.
        service.start()
        try:
            yield
        finally:
            service.stop()

    app = FastAPI(title="WA_bot panel", lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost"],
    )

    def require_csrf(request: Request) -> None:
        """Пропускает только запросы с токеном, выданным вместе со страницей."""
        token = request.headers.get("x-csrf-token", "")
        # Сравниваем байты: secrets.compare_digest не принимает строки с не-ASCII
        # символами и падал бы TypeError (500) на заголовке вроде «чужой» вместо
        # честного 403.
        if not token or not secrets.compare_digest(
            token.encode("utf-8", "surrogateescape"), csrf_token.encode()
        ):
            raise HTTPException(status_code=403, detail="Недействительный CSRF-токен")

    # --------------------------------------------------------------- состояние
    @app.get("/api/status")
    def api_status():
        # panel_version нужен странице, чтобы заметить собственное устаревание
        # (старый app.js в браузере), code_stale — чтобы напомнить про
        # перезапуск панели после правок кода.
        return {
            **service.status(),
            "panel_version": _panel_version(),
            "code_stale": _code_stale(),
        }

    # ---------------------------------------------------------------- сообщение
    @app.get("/api/message")
    def get_message():
        return {"text": urls_list.load_message()}

    @app.put("/api/message", dependencies=[Depends(require_csrf)])
    def put_message(payload: TextPayload):
        text = payload.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Текст сообщения не может быть пустым")
        _write_text_file(urls_list.MESSAGE_FILE, text)
        log.info("Панель обновила текст сообщения (%s символов)", len(text))
        return {"text": text}

    # ------------------------------------------------------------------- ссылки
    @app.get("/api/urls")
    def get_urls():
        text = _read_urls_text()
        return {"text": text, "urls": _parse_urls(text)}

    @app.put("/api/urls", dependencies=[Depends(require_csrf)])
    def put_urls(payload: TextPayload):
        parsed = _parse_urls(payload.text)
        if not parsed:
            raise HTTPException(status_code=400, detail="Нужна хотя бы одна ссылка-приглашение")
        _write_text_file(urls_list.URLS_FILE, payload.text.strip() + "\n")
        log.info("Панель обновила список ссылок: %s шт.", len(parsed))
        return {"text": _read_urls_text(), "urls": parsed}

    @app.post("/api/urls/import", dependencies=[Depends(require_csrf)])
    def import_urls(payload: TextPayload):
        existing = _parse_urls(_read_urls_text())
        incoming = _extract_invites(payload.text)
        if not incoming:
            raise HTTPException(
                status_code=400,
                detail="Не нашёл ссылок вида chat.whatsapp.com/...",
            )
        added = [url for url in incoming if url not in existing]
        duplicates = [url for url in incoming if url in existing]
        merged = existing + added
        _write_text_file(urls_list.URLS_FILE, "\n".join(merged) + "\n")
        log.info("Панель импортировала ссылки: добавлено %s, всего %s", len(added), len(merged))
        return {"added": added, "duplicates": duplicates, "urls": merged}

    # ------------------------------------------------ отсортированные ссылки
    @app.get("/api/groups")
    def get_groups():
        """Списки ссылок, которые заполняет скрипт вступления в группы."""
        return {
            "only_admins": group_lists.load(group_lists.ONLY_ADMINS_FILE),
            "closed": group_lists.load(group_lists.CLOSED_FILE),
            "open": group_lists.load(group_lists.OPEN_FILE),
        }

    # --------------------------------------------------------------- расписание
    @app.get("/api/schedule")
    def get_schedule():
        cfg = service.get_schedule()
        status = service.status()
        return {
            **cfg,
            "next_run": status["next_run"],
            "next_run_join": status["next_run_join"],
            "timezone": status["timezone"],
        }

    @app.put("/api/schedule", dependencies=[Depends(require_csrf)])
    def put_schedule(payload: SchedulePayload):
        try:
            cfg = service.update_schedule(payload.enabled, payload.times)
        except ConfigurationError as error:
            raise HTTPException(status_code=400, detail=str(error))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        status = service.status()
        return {
            **cfg,
            "next_run": status["next_run"],
            "next_run_join": status["next_run_join"],
            "timezone": status["timezone"],
        }

    @app.put("/api/schedule/join", dependencies=[Depends(require_csrf)])
    def put_join_schedule(payload: SchedulePayload):
        """Расписание скрипта вступления: правится независимо от рассылки."""
        try:
            cfg = service.update_join_schedule(payload.enabled, payload.times)
        except ConfigurationError as error:
            raise HTTPException(status_code=400, detail=str(error))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        status = service.status()
        return {
            **cfg,
            "next_run": status["next_run"],
            "next_run_join": status["next_run_join"],
            "timezone": status["timezone"],
        }

    # -------------------------------------------------------------------- прогон
    def start_manual_run(kind: str) -> dict:
        """Запускает прогон и пишет в журнал, принят он или отклонён.

        Без этих строк по журналу нельзя отличить «клик не дошёл до панели» от
        «панель отказала: уже идёт другой прогон».
        """
        try:
            result = service.start_run("manual", kind)
        except ServiceBusyError as error:
            log.warning("Панель: запуск (%s) отклонён: %s", kind, error)
            raise HTTPException(status_code=409, detail=str(error))
        log.info("Панель: запуск (%s, вручную) принят", kind)
        return result

    @app.post("/api/run", dependencies=[Depends(require_csrf)])
    def run_now():
        """Одиночный прогон рассылки (``main.run_once``)."""
        return start_manual_run("message")

    @app.post("/api/run/join", dependencies=[Depends(require_csrf)])
    def run_join_now():
        """Одиночный прогон скрипта вступления — без отправки сообщений."""
        return start_manual_run("join")

    # ------------------------------------------------------------------ профили
    @app.get("/api/profiles")
    def api_profiles():
        return {"profiles": service.profiles()}

    @app.post("/api/profiles", dependencies=[Depends(require_csrf)])
    def add_profile(payload: ProfilePayload):
        try:
            job_id = service.start_login(payload.name)
        except ConfigurationError as error:
            raise HTTPException(status_code=400, detail=str(error))
        except ServiceBusyError as error:
            raise HTTPException(status_code=409, detail=str(error))
        return {"job": job_id, "name": payload.name}

    @app.get("/api/profiles/login/{job_id}")
    def login_job(job_id: str):
        try:
            return service.login_status(job_id)
        except ConfigurationError as error:
            raise HTTPException(status_code=404, detail=str(error))

    @app.post("/api/profiles/{name}/check", dependencies=[Depends(require_csrf)])
    async def check_profile(name: str):
        # check_profile открывает headless-браузер (до ~15 с) — уводим из
        # event loop в поток, чтобы панель не «замирала».
        try:
            return await run_in_threadpool(service.check_profile, name)
        except ConfigurationError as error:
            raise HTTPException(status_code=400, detail=str(error))

    @app.delete("/api/profiles/{name}", dependencies=[Depends(require_csrf)])
    def remove_profile(name: str):
        try:
            service.delete_profile(name)
        except ConfigurationError as error:
            raise HTTPException(status_code=400, detail=str(error))
        except ServiceBusyError as error:
            raise HTTPException(status_code=409, detail=str(error))
        return {"deleted": name}

    # --------------------------------------------------------------- статистика
    @app.get("/api/stats")
    def api_stats():
        return service.stats()

    @app.get("/api/logs")
    def api_logs(lines: int = DEFAULT_LOG_LINES):
        lines = max(1, min(lines, MAX_LOG_LINES))
        return {"text": _tail(LOG_FILE, lines), "lines": lines}

    # -------------------------------------------------------------------- панель
    @app.post("/api/stop", dependencies=[Depends(require_csrf)])
    def stop_panel():
        """Выключает панель: то же, что Ctrl+C в окне терминала или ``./stop.sh``.

        Панель — и есть процесс бота, поэтому остановка панели освобождает
        блокировку ``logs/wa_bot.lock`` и возвращает запуск ``run.sh`` /
        ``join.sh`` / второй ``./web.sh``. Ответ успевает уйти в браузер:
        uvicorn замечает флаг не мгновенно, и только потом закрывает соединения
        и вызывает lifespan, где ``service.stop()`` ждёт текущий прогон до
        15 секунд. Поэтому страница показывает «останавливается» и опрашивает
        ``/api/status``, пока панель не перестанет отвечать.
        """
        status = service.status()
        if not stopper.request():
            raise HTTPException(status_code=409, detail="Панель уже останавливается")
        log.info(
            "Панель: команда остановки из интерфейса (прогон: %s)",
            "идёт" if status["running"] else "нет",
        )
        return {"stopping": True, "running": status["running"], "kind": status["kind"]}

    @app.get("/", response_class=HTMLResponse)
    def index():
        html = (PANEL_DIR / "index.html").read_text(encoding=ENCODING)
        # Токен подставляем при отдаче: сторонний сайт не сможет его прочитать.
        # Версия статики гасит кэш браузера после правок app.js/app.css.
        return html.replace("__CSRF_TOKEN__", csrf_token).replace(
            "__PANEL_VERSION__", _panel_version()
        )

    @app.get("/panel/index.html", include_in_schema=False)
    def panel_index_alias():
        """Статикой эту страницу не отдаём: в ней остался бы ``__CSRF_TOKEN__``.

        Такую ссылку легко получить из истории браузера, а страница с
        нерабочим токеном молча отклоняет все изменяющие запросы (403).
        """
        return RedirectResponse("/", status_code=307)

    app.mount("/panel", StaticFiles(directory=PANEL_DIR), name="panel")
    return app


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Локальная веб-панель WA_bot")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="порт панели (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="не открывать браузер автоматически",
    )
    return parser.parse_args(argv)


def _pick_port(preferred: int) -> int:
    """Ищет свободный порт на loopback, начиная с ``preferred``."""
    for port in range(preferred, preferred + PORT_ATTEMPTS):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise SystemExit(f"Не нашёл свободный порт рядом с {preferred}")


def _write_runtime(port: int) -> None:
    """Сохраняет порт панели: по нему повторный запуск откроет уже живую панель."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        RUNTIME_FILE.write_text(
            json.dumps({"port": port, "pid": os.getpid()}), encoding=ENCODING
        )
    except OSError:
        log.exception("Не удалось записать %s", RUNTIME_FILE)


def _read_runtime_port() -> int | None:
    try:
        data = json.loads(RUNTIME_FILE.read_text(encoding=ENCODING))
        return int(data["port"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _open_browser(url: str) -> None:
    """Открывает браузер чуть позже, чтобы сервер успел поднять сокет."""

    def _open() -> None:
        try:
            webbrowser.open(url)
        except Exception:
            log.warning("Не удалось открыть браузер автоматически: %s", url)

    threading.Timer(1.0, _open).start()


def _install_shutdown_handler(stopper: PanelStopper) -> None:
    """Просит панель остановиться по SIGHUP (закрытие окна терминала)."""

    def _request_shutdown(signum, _frame) -> None:
        # Через тот же PanelStopper, что и кнопка в интерфейсе: одна причина
        # остановки — одна строка в журнале.
        log.info("Получен сигнал %s — останавливаю панель", signum)
        stopper.request()

    # SIGINT/SIGTERM uvicorn обрабатывает сам; на Windows SIGHUP нет.
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _request_shutdown)


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging()
    port = _pick_port(args.port)
    url = f"http://127.0.0.1:{port}/"

    with run_lock() as acquired:
        if not acquired:
            log.error("Бот уже работает (%s) — второй экземпляр не нужен.", owner_description())
            existing = _read_runtime_port()
            if existing:
                log.info("Панель уже открыта: http://127.0.0.1:%s/", existing)
                if not args.no_browser:
                    _open_browser(f"http://127.0.0.1:{existing}/")
            log.info(
                "Остановить: кнопка «Остановить панель» в интерфейсе "
                "или ./stop.sh (Windows: stop.bat)"
            )
            return 1

        service = BotService()
        # Панель и бот — один процесс, поэтому кнопка «Остановить панель» гасит
        # и планировщик, и блокировку: серверу остаётся лишь попросить выход.
        stopper = PanelStopper()
        app = create_app(service, stopper)
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        stopper.attach(server)
        _install_shutdown_handler(stopper)

        _write_runtime(port)
        log.info("Панель доступна: %s", url)
        log.info("Остановить: Ctrl+C, кнопка «Остановить панель» в интерфейсе")
        if not args.no_browser:
            _open_browser(url)

        try:
            server.run()
        finally:
            # Даже если lifespan не успел завершиться, снимаем блокировку сами.
            service.stop()
            try:
                RUNTIME_FILE.unlink()
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



