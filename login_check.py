from pathlib import Path

from logger import get_logger

log = get_logger(__name__)

# Каталог профилей держим рядом с кодом, а не относительно текущего каталога
# запуска (та же логика, что и для логов в logger.py).
PROJECT_ROOT = Path(__file__).resolve().parent
PROFILES_DIR = PROJECT_ROOT / "profiles"

# Сколько ждём появления QR-кода: если он не появился — считаем, что вход выполнен.
QR_WAIT_TIMEOUT_MS = 15_000


def ensure_profiles_dir(path=PROFILES_DIR) -> Path:
    """Создаёт каталог профилей, если его нет, и возвращает его путь.

    Внутри профилей лежит сессия WhatsApp Web, поэтому сам каталог находится
    в .gitignore. Значит, у человека, только что скачавшего проект, его ещё
    нет — создаём его сами, чтобы бот не падал на FileNotFoundError.
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def check_profile(playwright, profile: Path) -> bool | None:
    """Открывает WhatsApp Web в профиле и определяет, выполнен ли вход.

    :return: ``True`` — вход выполнен (QR-код не появился),
        ``False`` — показан QR-код, нужен вход,
        ``None`` — профиль проверить не удалось (страница не открылась).
    """
    context = playwright.firefox.launch_persistent_context(
        user_data_dir=profile,
        headless=True,
    )

    try:
        page = context.new_page()
        try:
            page.goto("https://web.whatsapp.com")
        except Exception as error:
            # Сеть, DNS, таймаут загрузки и т.п. Профиль не классифицируем,
            # чтобы не уронить проверку остальных аккаунтов.
            log.exception(
                "Не удалось открыть WhatsApp Web для профиля %s: %s", profile, error
            )
            return None

        qr = page.locator('canvas[aria-label="Scan this QR code to link a device!"]')
        try:
            qr.wait_for(timeout=QR_WAIT_TIMEOUT_MS)
        except Exception:
            return True
        return False
    finally:
        # Браузер закрываем всегда, в том числе при ошибке загрузки страницы.
        context.close()


def login_check(playwright, profiles_dir=PROFILES_DIR) -> dict[str, list]:
    logged_list = []
    unlogged_list = []
    # Ключи есть всегда, даже если в profiles/ пока нет ни одного профиля.
    sum_dict = {"logged": logged_list, "unlogged": unlogged_list}
    log.info("Запуск проверки номеров")

    profiles = ensure_profiles_dir(profiles_dir)

    for profile in sorted(profiles.iterdir()):
        if not profile.is_dir():
            # Служебные файлы вроде .gitkeep профилями не являются.
            continue

        is_logged_in = check_profile(playwright, profile)

        if is_logged_in is None:
            log.warning("Профиль %s пропущен: не удалось проверить", profile)
        elif is_logged_in:
            logged_list.append(profile)
            log.info("В аккаунт с номером %s вошли", profile)
        else:
            unlogged_list.append(profile)
            log.warning("В аккаунт с номером %s НЕ вошли", profile)

    log.info("Проверка номеров окончена")

    return sum_dict