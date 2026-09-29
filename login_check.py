from pathlib import Path

from automation import inspect_profile
from logger import get_logger
from paths import PROFILES_DIR

log = get_logger(__name__)

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
    status, _detail = inspect_profile(playwright, Path(profile))
    if status == "authorized":
        return True
    if status == "needs_login":
        return False
    return None


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

        try:
            is_logged_in = check_profile(playwright, profile)
        except Exception:
            log.exception("Профиль %s пропущен: ошибка проверки", profile)
            is_logged_in = None

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
