"""Application service coordinating scheduling, profiles, and bot runs."""

import random
import shutil
import stat
import threading
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from tzlocal import get_localzone, reload_localzone

import script
from automation import authorize_profile, inspect_profile, send_to_group
from database import Database, utc_now
from domain import next_schedule_at, schedule_slot_key
from logger import get_logger
from paths import PROFILES_DIR

log = get_logger(__name__)


class ServiceBusyError(RuntimeError):
    pass


class ConfigurationError(RuntimeError):
    pass


class BotService:
    def __init__(self, database: Database):
        self.database = database
        self.stop_event = threading.Event()
        self.schedule_changed = threading.Event()
        self._state_lock = threading.Lock()
        self._operation: str | None = None
        self._operation_thread: threading.Thread | None = None
        self._scheduler_thread: threading.Thread | None = None
        self._authorization_confirmations: dict[int, threading.Event] = {}

    def start(self) -> None:
        self.database.prune_history(30)
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            name="wa-bot-scheduler",
            daemon=True,
        )
        self._scheduler_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.schedule_changed.set()
        thread = self._operation_thread
        if thread and thread.is_alive():
            log.info("Ожидаем мягкой остановки текущей операции")
            thread.join(timeout=15)
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            self._scheduler_thread.join(timeout=3)

    def status(self) -> dict:
        settings = self.database.get_settings()
        accounts = self.database.list_accounts(enabled_only=True)
        _, groups_count = self.database.list_groups(enabled_only=True, page_size=1)
        timezone = get_localzone()
        now = datetime.now(timezone)
        next_run = None
        if settings["schedule_enabled"]:
            with suppress(ValueError):
                next_run = next_schedule_at(
                    now, settings["schedule_days"], settings["schedule_times"]
                ).isoformat()
        with self._state_lock:
            operation = self._operation
        return {
            "operation": operation,
            "running": operation is not None,
            "enabled_accounts": len(accounts),
            "enabled_groups": groups_count,
            "planned_attempts": len(accounts) * groups_count,
            "next_run": next_run,
            "timezone": str(timezone),
        }

    def notify_schedule_changed(self) -> None:
        self.schedule_changed.set()

    def create_account(self, name: str, phone: str | None = None) -> int:
        name = name.strip()
        if not name:
            raise ConfigurationError("Укажите имя аккаунта")
        with self._state_lock:
            if self._operation:
                raise ServiceBusyError("Другая операция уже выполняется")
        if len(self.database.list_accounts()) >= 10:
            raise ConfigurationError("Для MVP поддерживается не больше 10 аккаунтов")
        profile = PROFILES_DIR / f"account-{uuid4().hex}"
        profile.mkdir(parents=True, exist_ok=False)
        try:
            account_id = self.database.add_account(
                name, phone.strip() if phone else None, profile
            )
        except Exception:
            profile.rmdir()
            raise
        self.authorize_account(account_id)
        return account_id

    def authorize_account(self, account_id: int) -> None:
        account = self.database.get_account(account_id)
        if not account:
            raise ConfigurationError("Аккаунт не найден")
        confirmation = threading.Event()
        self._begin_operation(
            f"Авторизация: {account['name']}",
            self._authorize_worker,
            account,
            confirmation,
            authorization=(account_id, confirmation),
        )

    def confirm_account_authorized(self, account_id: int) -> None:
        account = self.database.get_account(account_id)
        if not account:
            raise ConfigurationError("Аккаунт не найден")
        with self._state_lock:
            confirmation = self._authorization_confirmations.get(account_id)
            thread = self._operation_thread
        if confirmation is None:
            if account["status"] == "authorized":
                return
            raise ConfigurationError("Для этого аккаунта окно авторизации не открыто")

        log.info("Оператор подтвердил вход аккаунта %s", account["name"])
        confirmation.set()
        self.database.update_account(
            account_id,
            status="authorized",
            status_detail="Вход подтверждён оператором",
            status_checked_at=utc_now(),
        )
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=10)
            if thread.is_alive():
                log.warning(
                    "Окно авторизации аккаунта %s ещё закрывается",
                    account["name"],
                )

    def check_account(self, account_id: int) -> None:
        account = self.database.get_account(account_id)
        if not account:
            raise ConfigurationError("Аккаунт не найден")
        self._begin_operation(
            f"Проверка: {account['name']}",
            self._check_worker,
            account,
        )

    def delete_account(self, account_id: int) -> None:
        with self._state_lock:
            if self._operation:
                raise ServiceBusyError("Сначала дождитесь окончания текущей операции")
            account = self.database.get_account(account_id)
            if not account:
                return
            profile = Path(account["profile_path"]).resolve()
            profiles_root = PROFILES_DIR.resolve()
            if profile.parent != profiles_root:
                raise ConfigurationError("Некорректный путь профиля")
            if profile.exists():
                shutil.rmtree(profile, onexc=_remove_readonly)
            self.database.delete_account(account_id)

    def start_run(self, trigger="manual") -> int:
        accounts = self.database.list_accounts(enabled_only=True)
        groups, _ = self.database.list_groups(enabled_only=True, page_size=100_000)
        settings = self.database.get_settings()
        images = self.database.list_images()
        planned = len(accounts) * len(groups)
        if not accounts:
            raise ConfigurationError("Нет включённых WhatsApp-аккаунтов")
        if not groups:
            raise ConfigurationError("Нет включённых групп")
        if not settings["message_text"].strip() and not images:
            raise ConfigurationError("Сообщение пусто: добавьте текст или изображение")
        if planned > int(settings["max_attempts"]):
            raise ConfigurationError(
                f"Запланировано {planned} попыток, безопасный лимит — "
                f"{settings['max_attempts']}"
            )

        with self._state_lock:
            if self._operation:
                if trigger == "scheduled":
                    return self.database.create_run(
                        trigger,
                        "skipped",
                        planned,
                        "Предыдущая операция ещё выполняется",
                    )
                raise ServiceBusyError("Другая операция уже выполняется")
            run_id = self.database.create_run(trigger, "running", planned)
            self._operation = f"Рассылка #{run_id}"
            thread = threading.Thread(
                target=self._run_worker,
                args=(run_id, accounts, groups, settings, images),
                name=f"wa-bot-run-{run_id}",
                daemon=True,
            )
            self._operation_thread = thread
            thread.start()
            return run_id

    def _begin_operation(self, label, target, *args, authorization=None) -> None:
        with self._state_lock:
            if self._operation:
                raise ServiceBusyError("Другая операция уже выполняется")
            self._operation = label
            if authorization is not None:
                account_id, confirmation = authorization
                self._authorization_confirmations[account_id] = confirmation
                self.database.update_account(
                    account_id,
                    status="authorizing",
                    status_detail="После входа нажмите кнопку подтверждения в панели",
                )
            thread = threading.Thread(
                target=target,
                args=args,
                name="wa-bot-account-operation",
                daemon=True,
            )
            self._operation_thread = thread
            thread.start()

    def _finish_operation(self) -> None:
        with self._state_lock:
            self._operation = None
            self._operation_thread = None

    def _authorize_worker(self, account: dict, confirmation: threading.Event) -> None:
        try:
            with sync_playwright() as playwright:
                status, detail = authorize_profile(
                    playwright,
                    Path(account["profile_path"]),
                    self.stop_event,
                    confirmation,
                )
            self.database.update_account(
                account["id"],
                status=status,
                status_detail=detail,
                status_checked_at=utc_now(),
            )
        except Exception as error:
            log.exception("Ошибка авторизации аккаунта %s", account["name"])
            self.database.update_account(
                account["id"],
                status="error",
                status_detail=str(error),
                status_checked_at=utc_now(),
            )
        finally:
            with self._state_lock:
                self._authorization_confirmations.pop(account["id"], None)
            self._finish_operation()

    def _check_worker(self, account: dict) -> None:
        self.database.update_account(
            account["id"], status="checking", status_detail=None
        )
        try:
            with sync_playwright() as playwright:
                status, detail = inspect_profile(
                    playwright, Path(account["profile_path"])
                )
            self.database.update_account(
                account["id"],
                status=status,
                status_detail=detail,
                status_checked_at=utc_now(),
            )
        except Exception as error:
            log.exception("Ошибка проверки аккаунта %s", account["name"])
            self.database.update_account(
                account["id"],
                status="error",
                status_detail=str(error),
                status_checked_at=utc_now(),
            )
        finally:
            self._finish_operation()

    def _run_worker(self, run_id, accounts, groups, settings, images) -> None:
        final_status = "completed"
        run_stats = {}
        try:
            image_paths = [Path(item["stored_path"]) for item in images]
            with sync_playwright() as playwright:
                for account in accounts:
                    if self.stop_event.is_set():
                        final_status = "cancelled"
                        break
                    context = None
                    try:
                        context = playwright.firefox.launch_persistent_context(
                            user_data_dir=Path(account["profile_path"]),
                            headless=True,
                        )
                    except PlaywrightError as error:
                        self.database.update_account(
                            account["id"], status="error", status_detail=str(error)
                        )
                        self._record_account_failure(run_id, account, groups, str(error))
                        run_stats[account["name"]] = None
                        continue

                    profile = Path(account["profile_path"])
                    try:
                        profile_stats = script.start_profile_run(profile)
                        log.info("Профиль %s: запускаем сценарий", account["name"])
                        for group in groups:
                            if self.stop_event.is_set():
                                final_status = "cancelled"
                                break
                            self._run_attempt(
                                run_id,
                                account,
                                group,
                                context,
                                settings,
                                image_paths,
                                profile_stats,
                            )
                            if self.stop_event.wait(
                                random.uniform(
                                    float(settings["delay_min"]),
                                    float(settings["delay_max"]),
                                )
                            ):
                                final_status = "cancelled"
                                break
                    finally:
                        run_stats[account["name"]] = script.log_stats(profile)
                        try:
                            context.close()
                        except PlaywrightError:
                            log.warning("Не удалось закрыть браузер аккаунта %s", account["name"])
        except Exception:
            final_status = "failed"
            log.exception("Прогон #%s завершился необработанной ошибкой", run_id)
        finally:
            if run_stats:
                summary = script.save_run_summary(run_stats)
                log.info(
                    "Сводка прогона #%s: выгнан из %s групп(ы) суммарно по %s "
                    "профилям (файл %s)",
                    run_id,
                    summary["total_kicked"],
                    len(run_stats),
                    script.SUMMARY_FILE,
                )
            self.database.finish_run(run_id, final_status)
            self._finish_operation()

    def _run_attempt(
        self,
        run_id,
        account,
        group,
        context,
        settings,
        image_paths,
        profile_stats,
    ):
        attempt_id = self.database.add_attempt(run_id, account, group)
        outcome = send_to_group(
            context,
            group["invite_url"],
            settings["message_text"],
            image_paths,
            settings["message_mode"],
            self.stop_event,
        )
        if outcome.safe_to_retry and not self.stop_event.is_set():
            log.warning("Безопасно повторяем попытку группы %s", group["id"])
            outcome = send_to_group(
                context,
                group["invite_url"],
                settings["message_text"],
                image_paths,
                settings["message_mode"],
                self.stop_event,
            )
        self.database.finish_attempt(attempt_id, outcome.status, outcome.detail)
        self.database.update_group(
            group["id"],
            name=outcome.group_name or group.get("name"),
            last_status=outcome.status,
        )
        if outcome.status == "needs_login":
            self.database.update_account(
                account["id"],
                status="needs_login",
                status_detail=outcome.detail,
                status_checked_at=utc_now(),
            )
        if outcome.status == "kicked":
            script.record_kicked(
                account["profile_path"],
                profile_stats,
                outcome.group_name or group.get("name"),
            )
        elif outcome.status != "sent":
            log.warning(
                "Группа %s пропущена (%s): %s",
                outcome.group_name or group.get("name") or group["id"],
                outcome.status,
                outcome.detail or "без подробностей",
            )
        return outcome

    def _record_account_failure(self, run_id, account, groups, error):
        for group in groups:
            attempt_id = self.database.add_attempt(run_id, account, group)
            self.database.finish_attempt(attempt_id, "error", error)

    def _scheduler_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                settings = self.database.get_settings()
                if settings["schedule_enabled"]:
                    timezone = reload_localzone()
                    now = datetime.now(timezone)
                    slot = schedule_slot_key(
                        now,
                        settings["schedule_days"],
                        settings["schedule_times"],
                    )
                    if slot and slot != settings.get("last_scheduler_slot"):
                        self.database.set_setting("last_scheduler_slot", slot)
                        try:
                            self.start_run("scheduled")
                        except (ConfigurationError, ServiceBusyError) as error:
                            log.warning("Запуск по расписанию пропущен: %s", error)
                self.schedule_changed.wait(timeout=5)
                self.schedule_changed.clear()
            except Exception:
                log.exception("Ошибка планировщика; повтор через 5 секунд")
                self.stop_event.wait(5)


def _remove_readonly(function, path, _error) -> None:
    """Retry removal of files marked read-only by Firefox on Windows."""
    item = Path(path)
    item.chmod(item.stat().st_mode | stat.S_IWRITE)
    function(path)
