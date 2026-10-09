"""Операционный слой панели: планировщик, прогоны, QR-вход, статусы профилей.

Панель не меняет логику бота: прогон целиком выполняет ``main.run_once()``,
статус профиля определяет ``login_check.check_profile``, удаление —
``login.delete_profile``, добавление — видимый Firefox на этом же компьютере с
ожиданием входа. Задача модуля — сериализовать доступ к Playwright (sync API
нельзя использовать из нескольких потоков одновременно) и отдавать панели
понятное состояние.

Планировщик здесь свой (не ``scheduler.run_forever``): ему нужно уметь
просыпаться при правке ``settings.json`` из интерфейса и останавливаться вместе
с панелью. При этом расчёт ближайшего слота переиспользует существующие
``scheduler.parse_run_at`` / ``scheduler.next_run_at``.
"""

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

import join_groups
import login
import login_check
import script
import settings as settings_store
from logger import get_logger
from main import run_once
from scheduler import next_run_at, parse_run_at

log = get_logger(__name__)

WHATSAPP_WEB = "https://web.whatsapp.com"

# Сколько ждём, пока оператор отсканирует QR-код при добавлении номера.
LOGIN_TIMEOUT_SECONDS = 300

# Пауза между проверками состояния страницы входа.
LOGIN_POLL_SECONDS = 0.75

# Планировщик просыпается не реже, чем раз в столько секунд: и чтобы быстро
# остановиться вместе с панелью, и чтобы заметить правку расписания.
SCHEDULER_TICK_SECONDS = 5.0

# Селекторы WhatsApp Web (те же признаки, что и в login_check.py).
AUTH_SELECTOR = '#pane-side, [data-testid="chat-list"]'
QR_SELECTOR = 'canvas[data-ref], canvas[aria-label*="QR" i], canvas[aria-label*="код" i]'
LOGIN_TEXT_PATTERN = re.compile(
    r"(scan.*qr|сканир.*qr|link with phone|привязать.*телефон)", re.IGNORECASE
)


class ServiceBusyError(RuntimeError):
    """Операция не начата: другая операция уже идёт."""


class ConfigurationError(RuntimeError):
    """Неверные входные данные."""


def _is_visible(locator) -> bool:
    """``True``, если первый элемент локатора существует и виден."""
    try:
        return locator.count() > 0 and locator.first.is_visible()
    except Exception:
        # Страница могла закрыться между count() и is_visible() — считаем невидимым.
        return False


def detect_profile_state(page) -> str | None:
    """Определяет состояние профиля по странице WhatsApp Web.

    :return: ``"authorized"`` — вход выполнен, ``"needs_login"`` — показан
        QR-код, ``None`` — страница ещё не определилась.
    """
    if _is_visible(page.locator(AUTH_SELECTOR)):
        return "authorized"
    if _is_visible(page.locator(QR_SELECTOR)) or _is_visible(
        page.get_by_text(LOGIN_TEXT_PATTERN)
    ):
        return "needs_login"
    return None


def _next_run(cfg, enabled_key, times_key) -> datetime | None:
    """Ближайший слот расписания ``(enabled_key, times_key)`` или ``None``.

    Некорректные времена трактуются как «расписания нет»: опечатка в
    ``settings.json`` не должна ронять ни панель, ни планировщик.

    :param cfg: настройки из :func:`settings.load`.
    :param enabled_key: ключ переключателя автозапуска (``"enabled"`` /
        ``"join_enabled"``).
    :param times_key: ключ списка времён (``"times"`` / ``"join_times"``).
    """
    if not cfg.get(enabled_key) or not cfg.get(times_key):
        return None
    try:
        times = parse_run_at(cfg[times_key])
    except ValueError:
        log.exception("Некорректное расписание %s=%s, жду правок", times_key, cfg.get(times_key))
        return None
    return next_run_at(times) if times else None


class BotService:
    """Состояние панели: планировщик, прогон, QR-вход, Playwright-лок."""

    def __init__(self, settings_path=None):
        self.settings_path = (
            Path(settings_path) if settings_path else settings_store.SETTINGS_FILE
        )
        self.stop_event = threading.Event()
        self.settings_changed = threading.Event()
        # sync Playwright нельзя использовать параллельно: прогон, проверка
        # профиля и QR-вход идут строго по очереди.
        self.playwright_lock = threading.Lock()

        self._state_lock = threading.Lock()
        self._scheduler_thread = None
        self._run_thread = None
        self._run_active = False
        self._run_trigger = None
        self._run_kind = None
        self._run_started_at = None
        self._login_jobs = {}
        self._login_counter = 0
        self._stopped = False

    # ------------------------------------------------------------------ жизнь
    def start(self) -> None:
        """Создаёт каталог профилей и запускает поток планировщика."""
        login_check.ensure_profiles_dir()
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            name="wa-bot-scheduler",
            daemon=True,
        )
        self._scheduler_thread.start()
        log.info("Планировщик панели запущен")

    def stop(self) -> None:
        """Останавливает планировщик и ждёт завершения текущего прогона.

        Вызывается дважды (lifespan приложения и ``finally`` в main), поэтому
        повторный вызов ничего не делает — иначе в лог сыпались бы дубли.
        """
        if self._stopped:
            return
        self._stopped = True
        self.stop_event.set()
        self.settings_changed.set()

        run_thread = self._run_thread
        if run_thread and run_thread.is_alive():
            log.info("Ожидаем завершения текущего прогона")
            run_thread.join(timeout=15)

        if self._scheduler_thread and self._scheduler_thread.is_alive():
            self._scheduler_thread.join(timeout=SCHEDULER_TICK_SECONDS + 2)
        log.info("Планировщик панели остановлен")

    # ------------------------------------------------------------- состояние
    def status(self) -> dict:
        """Снимок состояния для дашборда панели."""
        cfg = settings_store.load(self.settings_path)
        with self._state_lock:
            run_active = self._run_active
            trigger = self._run_trigger
            kind = self._run_kind
            started_at = self._run_started_at

        next_run = _next_run(cfg, "enabled", "times")
        next_run_join = _next_run(cfg, "join_enabled", "join_times")

        profiles = login.list_profiles(login_check.PROFILES_DIR)
        return {
            "running": run_active,
            "trigger": trigger,
            "kind": kind,
            "started_at": started_at.isoformat(timespec="seconds") if started_at else None,
            "next_run": next_run.isoformat(timespec="seconds") if next_run else None,
            "next_run_join": (
                next_run_join.isoformat(timespec="seconds") if next_run_join else None
            ),
            "schedule": cfg,
            "profiles_count": len(profiles),
            "timezone": datetime.now().astimezone().tzname(),
        }

    # ------------------------------------------------------------ расписание
    def get_schedule(self) -> dict:
        """Текущее расписание (как в ``settings.json``)."""
        return settings_store.load(self.settings_path)

    def update_schedule(self, enabled: bool, times) -> dict:
        """Проверяет и сохраняет расписание рассылки, будит планировщик.

        Расписание вступления (``join_enabled`` / ``join_times``) не трогается:
        два расписания правятся независимо.

        :raises ConfigurationError: если ни одно время не задано.
        :raises ValueError: если время не в формате ``ЧЧ:ММ``.
        """
        cfg = self._apply_schedule(enabled, times, "enabled", "times")
        log.info(
            "Расписание рассылки обновлено: enabled=%s, times=%s",
            cfg["enabled"],
            cfg["times"],
        )
        return cfg

    def update_join_schedule(self, enabled: bool, times) -> dict:
        """Проверяет и сохраняет расписание вступления, будит планировщик.

        :raises ConfigurationError: если ни одно время не задано.
        :raises ValueError: если время не в формате ``ЧЧ:ММ``.
        """
        cfg = self._apply_schedule(enabled, times, "join_enabled", "join_times")
        log.info(
            "Расписание вступления обновлено: enabled=%s, times=%s",
            cfg["join_enabled"],
            cfg["join_times"],
        )
        return cfg

    def _apply_schedule(self, enabled, times, enabled_key, times_key) -> dict:
        """Пишет пару ``(enabled_key, times_key)`` в ``settings.json`` целиком.

        Остальные ключи сохраняются: расписания рассылки и вступления живут в
        одном файле и правятся из разных полей панели.
        """
        parsed = parse_run_at(times)
        if not parsed:
            raise ConfigurationError("Укажите хотя бы одно время запуска")
        cfg = settings_store.load(self.settings_path)
        cfg[enabled_key] = bool(enabled)
        cfg[times_key] = [moment.strftime("%H:%M") for moment in parsed]
        settings_store.save(cfg, self.settings_path)
        # Даём планировщику понять, что расписание поменялось и его надо перечитать.
        self.settings_changed.set()
        return cfg

    # ----------------------------------------------------------------- прогон
    def start_run(self, trigger: str = "manual", kind: str = "message") -> dict:
        """Запускает прогон выбранного скрипта в отдельном потоке.

        :param trigger: ``"manual"`` или ``"scheduled"`` — только для интерфейса
            и логов.
        :param kind: ``"message"`` — рассылка (``main.run_once``), ``"join"`` —
            вступление в группы (:func:`join_groups.run_once`).
        :raises ServiceBusyError: если прогон или QR-вход уже выполняется.
        """
        with self._state_lock:
            if self._run_active:
                raise ServiceBusyError("Прогон уже выполняется")
            if any(job["status"] == "running" for job in self._login_jobs.values()):
                raise ServiceBusyError("Идёт добавление номера, дождитесь завершения")
            self._run_active = True
            self._run_trigger = trigger
            self._run_kind = kind
            self._run_started_at = datetime.now()
            started_at = self._run_started_at

        thread = threading.Thread(
            target=self._run_worker,
            args=(trigger, kind),
            name="wa-bot-run",
            daemon=True,
        )
        with self._state_lock:
            self._run_thread = thread
        thread.start()
        return {
            "trigger": trigger,
            "kind": kind,
            "started_at": started_at.isoformat(timespec="seconds"),
        }

    def _run_worker(self, trigger: str, kind: str) -> None:
        # Реальный прогон целиком делает выбранный скрипт: панель логику не копирует.
        job = join_groups.run_once if kind == "join" else run_once
        try:
            log.info("Прогон (%s, %s) начат", kind, trigger)
            with self.playwright_lock:
                job()
            log.info("Прогон (%s, %s) завершён", kind, trigger)
        except Exception:
            # Падение прогона не должно убивать панель и планировщик.
            log.exception("Прогон (%s, %s) завершился ошибкой", kind, trigger)
        finally:
            with self._state_lock:
                self._run_active = False
                self._run_trigger = None
                self._run_kind = None

    def _wait_current_run(self) -> None:
        """Ждёт завершения прогона либо остановки панели."""
        while not self.stop_event.is_set():
            with self._state_lock:
                thread = self._run_thread
            if thread is None or not thread.is_alive():
                return
            self.stop_event.wait(0.5)

    # ---------------------------------------------------------------- профили
    def profiles(self) -> list:
        """Список профилей с краткой статистикой (без открытия браузера)."""
        result = []
        for profile in login.list_profiles(login_check.PROFILES_DIR):
            stats = script.read_stats(profile) or {}
            result.append(
                {
                    "name": profile.name,
                    "kicked_count": stats.get("kicked_count"),
                    "last_date_change": stats.get("last_date_change"),
                }
            )
        return result

    def check_profile(self, name: str) -> dict:
        """Открывает профиль в headless-браузере и определяет статус входа.

        :return: ``{"name": ..., "status": "logged"|"unlogged"|"unknown"}``.
        """
        profile = self._profile_path(name)
        log.info("Проверка профиля %s", profile)
        with self.playwright_lock:
            with sync_playwright() as playwright:
                logged_in = login_check.check_profile(playwright, profile)
        if logged_in is None:
            status = "unknown"
        else:
            status = "logged" if logged_in else "unlogged"
        return {"name": profile.name, "status": status}

    def delete_profile(self, name: str) -> None:
        """Удаляет профиль (сессию WhatsApp) с диска.

        :raises ServiceBusyError: если идёт прогон — профиль может быть занят.
        """
        if not login.is_valid_name(name):
            raise ConfigurationError(f"Недопустимое имя профиля: {name!r}")
        with self._state_lock:
            if self._run_active:
                raise ServiceBusyError("Идёт прогон: удаление отменено")
        profile = login_check.PROFILES_DIR / name
        if not profile.is_dir():
            raise ConfigurationError(f"Профиль {name} не найден")
        if not login.delete_profile(profile, login_check.PROFILES_DIR):
            raise ConfigurationError(
                f"Не удалось удалить профиль {name}: каталог занят "
                "(закрыт Firefox или идёт проверка)"
            )

    def start_login(self, name: str) -> str:
        """Запускает добавление номера: видимый Firefox + ожидание QR-входа.

        :return: идентификатор задачи, по которому панель опрашивает статус.
        :raises ConfigurationError: если имя некорректно или профиль уже есть.
        :raises ServiceBusyError: если идёт прогон или другое добавление.
        """
        name = (name or "").strip()
        if not login.is_valid_name(name):
            raise ConfigurationError("Недопустимое имя профиля: пусто или содержит / \\")
        profile = login_check.PROFILES_DIR / name
        if profile.exists():
            raise ConfigurationError(f"Профиль {name} уже существует")

        with self._state_lock:
            if self._run_active:
                raise ServiceBusyError("Идёт прогон: добавление отменено")
            if any(job["status"] == "running" for job in self._login_jobs.values()):
                raise ServiceBusyError("Другое добавление уже выполняется")
            self._login_counter += 1
            job_id = str(self._login_counter)
            self._login_jobs[job_id] = {
                "name": name,
                "status": "running",
                "detail": "Окно Firefox открыто — отсканируйте QR-код телефоном",
            }

        login_check.ensure_profiles_dir()
        profile.mkdir(parents=True, exist_ok=True)
        threading.Thread(
            target=self._login_worker,
            args=(job_id, profile),
            name=f"wa-bot-login-{job_id}",
            daemon=True,
        ).start()
        log.info("Начато добавление профиля %s (задача %s)", profile, job_id)
        return job_id

    def login_status(self, job_id: str) -> dict:
        """Статус задачи добавления номера."""
        with self._state_lock:
            job = self._login_jobs.get(str(job_id))
            if job is None:
                raise ConfigurationError("Задача добавления не найдена")
            return dict(job)

    def _login_worker(self, job_id: str, profile: Path) -> None:
        try:
            state = self._authorize(profile)
            if state == "authorized":
                self._set_job(job_id, "done", "Вход выполнен")
            elif state == "stopped":
                self._set_job(job_id, "error", "Остановлено вместе с панелью")
            else:
                self._set_job(job_id, "timeout", "Время ожидания истекло — попробуйте снова")
        except Exception as error:
            log.exception("Ошибка при добавлении профиля %s", profile)
            self._set_job(job_id, "error", str(error))

    def _authorize(self, profile: Path) -> str:
        """Открывает видимый Firefox и ждёт, пока оператор войдёт в аккаунт."""
        with self.playwright_lock:
            with sync_playwright() as playwright:
                context = playwright.firefox.launch_persistent_context(
                    user_data_dir=profile,
                    headless=False,
                )
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(WHATSAPP_WEB)
                    deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
                    while time.monotonic() < deadline:
                        if self.stop_event.is_set():
                            return "stopped"
                        if detect_profile_state(page) == "authorized":
                            return "authorized"
                        page.wait_for_timeout(int(LOGIN_POLL_SECONDS * 1000))
                    return "needs_login"
                finally:
                    context.close()

    def _set_job(self, job_id: str, status: str, detail: str) -> None:
        with self._state_lock:
            if job_id in self._login_jobs:
                self._login_jobs[job_id].update({"status": status, "detail": detail})

    def _profile_path(self, name: str) -> Path:
        if not login.is_valid_name(name):
            raise ConfigurationError(f"Недопустимое имя профиля: {name!r}")
        profile = login_check.PROFILES_DIR / name
        if not profile.is_dir():
            raise ConfigurationError(f"Профиль {name} не найден")
        return profile

    # ------------------------------------------------------------ статистика
    def stats(self) -> dict:
        """История сводок прогонов и статистика по профилям."""
        summary = []
        try:
            data = json.loads(script.SUMMARY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                summary = data
        except FileNotFoundError:
            summary = []
        except (OSError, json.JSONDecodeError):
            log.exception("Не удалось прочитать сводку %s", script.SUMMARY_FILE)
            summary = []

        profiles = []
        for profile in login.list_profiles(login_check.PROFILES_DIR):
            stats = script.read_stats(profile) or {}
            profiles.append(
                {
                    "name": profile.name,
                    "kicked_count": stats.get("kicked_count"),
                    "last_date_change": stats.get("last_date_change"),
                }
            )
        # Панели достаточно последних прогонов, а не всей истории.
        return {"summary": summary[-30:], "profiles": profiles}

    # ----------------------------------------------------------- планировщик
    def _next_job(self, cfg):
        """Ближайший слот среди обоих расписаний: ``(moment, kind)`` или ``None``.

        Рассылка (``enabled`` / ``times``) и вступление (``join_enabled`` /
        ``join_times``) планируются вместе: панель держит один поток-планировщик
        на оба расписания, а ближайший слот сам сообщает свой ``kind``.
        """
        candidates = []
        for kind, enabled_key, times_key in (
            ("message", "enabled", "times"),
            ("join", "join_enabled", "join_times"),
        ):
            moment = _next_run(cfg, enabled_key, times_key)
            if moment is not None:
                candidates.append((moment, kind))
        if not candidates:
            return None
        return min(candidates, key=lambda item: item[0])

    def _scheduler_loop(self) -> None:
        while not self.stop_event.is_set():
            cfg = settings_store.load(self.settings_path)

            job = self._next_job(cfg)
            if job is None:
                self.settings_changed.wait(timeout=SCHEDULER_TICK_SECONDS)
                self.settings_changed.clear()
                continue

            moment, kind = job
            delay = max((moment - datetime.now()).total_seconds(), 0.0)
            log.info(
                "Следующий запуск (%s) по расписанию: %s (через %.1f мин)",
                kind,
                moment.strftime("%Y-%m-%d %H:%M"),
                delay / 60,
            )

            # Ждём слот, но просыпаемся раньше при остановке панели или правке
            # расписания — иначе смена времени ждала бы старого слота.
            while (
                delay > 0
                and not self.stop_event.is_set()
                and not self.settings_changed.is_set()
            ):
                chunk = min(delay, SCHEDULER_TICK_SECONDS)
                self.stop_event.wait(chunk)
                delay -= chunk

            if self.stop_event.is_set():
                break
            if self.settings_changed.is_set():
                self.settings_changed.clear()
                continue

            log.info(
                "Запуск (%s) по расписанию: %s",
                kind,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            try:
                self.start_run("scheduled", kind)
            except ServiceBusyError as error:
                log.warning("Запуск по расписанию пропущен: %s", error)
                self.stop_event.wait(SCHEDULER_TICK_SECONDS)
                continue
            self._wait_current_run()




