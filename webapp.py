"""FastAPI application for the local operator panel."""

import math
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile
from starlette.middleware.trustedhost import TrustedHostMiddleware

from database import Database
from domain import extract_invite_links, parse_schedule_times, validate_days
from logger import LOG_FILE, get_logger
from migration import migrate_legacy_data
from paths import MEDIA_DIR, PROJECT_ROOT, ensure_data_dirs
from service import BotService, ConfigurationError, ServiceBusyError

log = get_logger(__name__)
TEMPLATES = Jinja2Templates(directory=PROJECT_ROOT / "templates")
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMPORT_BYTES = 5 * 1024 * 1024
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
WEEKDAYS = [
    (0, "Пн"),
    (1, "Вт"),
    (2, "Ср"),
    (3, "Чт"),
    (4, "Пт"),
    (5, "Сб"),
    (6, "Вс"),
]


def create_app(database=None, service=None) -> FastAPI:
    if database is None:
        ensure_data_dirs()
        database = Database()
    else:
        database.path.parent.mkdir(parents=True, exist_ok=True)
    database.initialize()
    migrate_legacy_data(database)
    service = service or BotService(database)
    csrf_token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(_app):
        service.start()
        try:
            yield
        finally:
            service.stop()

    app = FastAPI(title="WA Bot", lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )
    app.mount(
        "/static",
        StaticFiles(directory=PROJECT_ROOT / "static"),
        name="static",
    )
    app.state.database = database
    app.state.service = service
    app.state.csrf_token = csrf_token

    def render(request: Request, template: str, **context):
        return TEMPLATES.TemplateResponse(
            request=request,
            name=template,
            context={
                "csrf_token": csrf_token,
                "current_path": request.url.path,
                "flash_message": request.query_params.get("message"),
                "flash_error": request.query_params.get("error"),
                **context,
            },
        )

    async def checked_form(request: Request):
        form = await request.form()
        if not secrets.compare_digest(str(form.get("csrf_token", "")), csrf_token):
            raise HTTPException(status_code=403, detail="Недействительный CSRF-токен")
        return form

    def redirect(path: str, *, message=None, error=None):
        query = {key: value for key, value in {"message": message, "error": error}.items() if value}
        target = f"{path}?{urlencode(query)}" if query else path
        return RedirectResponse(target, status_code=303)

    @app.get("/")
    async def dashboard(request: Request):
        return render(
            request,
            "dashboard.html",
            status=service.status(),
            settings=database.get_settings(),
            runs=database.recent_runs(10),
        )

    @app.get("/api/status")
    async def api_status():
        return service.status()

    @app.post("/run")
    async def run_now(request: Request):
        await checked_form(request)
        try:
            run_id = service.start_run("manual")
        except (ConfigurationError, ServiceBusyError) as error:
            return redirect("/", error=str(error))
        return redirect("/", message=f"Прогон #{run_id} запущен")

    @app.get("/message")
    async def message_page(request: Request):
        return render(
            request,
            "message.html",
            settings=database.get_settings(),
            images=database.list_images(),
        )

    @app.post("/message")
    async def save_message(request: Request):
        form = await checked_form(request)
        mode = str(form.get("message_mode", "caption"))
        if mode not in {"caption", "separate"}:
            return redirect("/message", error="Неизвестный режим сообщения")
        database.set_settings(
            {
                "message_text": str(form.get("message_text", "")),
                "message_mode": mode,
            }
        )
        return redirect("/message", message="Сообщение сохранено")

    @app.post("/message/images")
    async def upload_images(request: Request):
        form = await checked_form(request)
        uploads = [item for item in form.getlist("images") if isinstance(item, UploadFile)]
        if not uploads:
            return redirect("/message", error="Выберите хотя бы одно изображение")
        try:
            for upload in uploads:
                await _save_image(upload, database)
        except ValueError as error:
            return redirect("/message", error=str(error))
        return redirect("/message", message=f"Добавлено изображений: {len(uploads)}")

    @app.post("/message/images/{image_id}/delete")
    async def delete_image(request: Request, image_id: int):
        await checked_form(request)
        image = database.get_image(image_id)
        if image:
            path = Path(image["stored_path"])
            if path.parent.resolve() == MEDIA_DIR.resolve():
                path.unlink(missing_ok=True)
            database.delete_image(image_id)
        return redirect("/message", message="Изображение удалено")

    @app.get("/media/{image_id}")
    async def media(image_id: int):
        image = database.get_image(image_id)
        if not image:
            raise HTTPException(status_code=404)
        path = Path(image["stored_path"])
        if path.parent.resolve() != MEDIA_DIR.resolve() or not path.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(path)

    @app.get("/schedule")
    async def schedule_page(request: Request):
        return render(
            request,
            "schedule.html",
            settings=database.get_settings(),
            status=service.status(),
            weekdays=WEEKDAYS,
        )

    @app.post("/schedule")
    async def save_schedule(request: Request):
        form = await checked_form(request)
        try:
            days = validate_days(form.getlist("days"))
            times = parse_schedule_times(str(form.get("times", "")))
            delay_min = float(form.get("delay_min", 4))
            delay_max = float(form.get("delay_max", 8))
            max_attempts = int(form.get("max_attempts", 500))
            if delay_min < 0 or delay_max < delay_min:
                raise ValueError("Некорректный диапазон задержки")
            if max_attempts < 1:
                raise ValueError("Лимит попыток должен быть больше нуля")
        except (TypeError, ValueError) as error:
            return redirect("/schedule", error=str(error))
        database.set_settings(
            {
                "schedule_enabled": form.get("schedule_enabled") == "on",
                "schedule_days": days,
                "schedule_times": times,
                "delay_min": delay_min,
                "delay_max": delay_max,
                "max_attempts": max_attempts,
            }
        )
        service.notify_schedule_changed()
        return redirect("/schedule", message="Расписание сохранено")

    @app.get("/accounts")
    async def accounts_page(request: Request):
        return render(
            request,
            "accounts.html",
            accounts=database.list_accounts(),
            status=service.status(),
        )

    @app.post("/accounts")
    async def add_account(request: Request):
        form = await checked_form(request)
        try:
            service.create_account(
                str(form.get("name", "")), str(form.get("phone", "")) or None
            )
        except (ConfigurationError, ServiceBusyError) as error:
            return redirect("/accounts", error=str(error))
        return redirect(
            "/accounts",
            message="Открыто окно WhatsApp Web; отсканируйте QR-код",
        )

    @app.post("/accounts/{account_id}")
    async def edit_account(request: Request, account_id: int):
        form = await checked_form(request)
        name = str(form.get("name", "")).strip()
        if not name:
            return redirect("/accounts", error="Имя аккаунта не может быть пустым")
        database.update_account(
            account_id,
            name=name,
            phone=str(form.get("phone", "")).strip() or None,
            enabled=1 if form.get("enabled") == "on" else 0,
        )
        return redirect("/accounts", message="Аккаунт сохранён")

    @app.post("/accounts/{account_id}/authorize")
    async def authorize_account(request: Request, account_id: int):
        await checked_form(request)
        try:
            service.authorize_account(account_id)
        except (ConfigurationError, ServiceBusyError) as error:
            return redirect("/accounts", error=str(error))
        return redirect("/accounts", message="Открыто окно авторизации")

    @app.post("/accounts/{account_id}/confirm-authorized")
    async def confirm_account_authorized(request: Request, account_id: int):
        await checked_form(request)
        try:
            service.confirm_account_authorized(account_id)
        except (ConfigurationError, ServiceBusyError) as error:
            return redirect("/accounts", error=str(error))
        return redirect(
            "/accounts",
            message="Вход подтверждён; окно авторизации закрыто",
        )

    @app.post("/accounts/{account_id}/check")
    async def check_account(request: Request, account_id: int):
        await checked_form(request)
        try:
            service.check_account(account_id)
        except (ConfigurationError, ServiceBusyError) as error:
            return redirect("/accounts", error=str(error))
        return redirect("/accounts", message="Проверка аккаунта запущена")

    @app.post("/accounts/{account_id}/delete")
    async def delete_account(request: Request, account_id: int):
        await checked_form(request)
        try:
            service.delete_account(account_id)
        except (ConfigurationError, ServiceBusyError, OSError) as error:
            return redirect("/accounts", error=str(error))
        return redirect("/accounts", message="Аккаунт и его сессия удалены")

    @app.get("/groups")
    async def groups_page(request: Request, q: str = "", page: int = 1):
        page = max(page, 1)
        groups, total = database.list_groups(search=q, page=page, page_size=100)
        return render(
            request,
            "groups.html",
            groups=groups,
            total=total,
            q=q,
            page=page,
            pages=max(1, math.ceil(total / 100)),
        )

    @app.post("/groups/import")
    async def import_groups(request: Request):
        form = await checked_form(request)
        content = str(form.get("links", ""))
        upload = form.get("file")
        if isinstance(upload, UploadFile) and upload.filename:
            payload = await upload.read(MAX_IMPORT_BYTES + 1)
            if len(payload) > MAX_IMPORT_BYTES:
                return redirect("/groups", error="Текстовый файл больше 5 МБ")
            try:
                content += "\n" + payload.decode("utf-8-sig")
            except UnicodeDecodeError:
                return redirect("/groups", error="Текстовый файл должен быть в UTF-8")
        links = extract_invite_links(content)
        if not links:
            return redirect("/groups", error="Ссылки WhatsApp не найдены")
        added, duplicates = database.import_groups(links)
        return redirect(
            "/groups",
            message=f"Добавлено: {added}; уже были в списке: {duplicates}",
        )

    @app.post("/groups/bulk")
    async def bulk_groups(request: Request):
        form = await checked_form(request)
        try:
            ids = [int(value) for value in form.getlist("group_ids")]
            changed = database.bulk_groups(ids, str(form.get("action", "")))
        except ValueError as error:
            return redirect("/groups", error=str(error))
        return redirect("/groups", message=f"Изменено групп: {changed}")

    @app.post("/groups/{group_id}")
    async def edit_group(request: Request, group_id: int):
        form = await checked_form(request)
        database.update_group(
            group_id,
            name=str(form.get("name", "")).strip() or None,
            enabled=1 if form.get("enabled") == "on" else 0,
        )
        return redirect("/groups", message="Группа сохранена")

    @app.get("/history")
    async def history_page(request: Request):
        return render(request, "history.html", runs=database.recent_runs(100))

    @app.get("/history/{run_id}")
    async def run_page(request: Request, run_id: int):
        run, attempts = database.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404)
        return render(request, "run.html", run=run, attempts=attempts)

    @app.get("/logs")
    async def logs_page(request: Request):
        return render(request, "logs.html", log_text=_tail(LOG_FILE, 500))

    return app


async def _save_image(upload: UploadFile, database: Database) -> None:
    original = Path(upload.filename or "image").name
    suffix = Path(original).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError(f"{original}: поддерживаются JPG, PNG, WEBP и GIF")
    destination = MEDIA_DIR / f"{uuid4().hex}{suffix}"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    size = 0
    try:
        with temporary.open("wb") as target:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    raise ValueError(f"{original}: файл больше 25 МБ")
                target.write(chunk)
        temporary.replace(destination)
        database.add_image(original, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


def _tail(path: Path, lines: int) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return "".join(handle.readlines()[-lines:])
    except FileNotFoundError:
        return "Лог пока пуст."
