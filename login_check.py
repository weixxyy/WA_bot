from logger import get_logger
from pathlib import Path

log = get_logger(__name__)

def login_check(playwright) -> dict[str,list]:
    logged_list = []
    unlogged_list = []
    sum_dict = {}
    log.info("Запуск проверки номеров")

    profiles = Path("profiles")

    for profile in profiles.iterdir():
        context = playwright.firefox.launch_persistent_context(
            user_data_dir=profile,
            headless=True,
        )

        page = context.new_page()
        page.goto("https://web.whatsapp.com")
        qr = page.locator('canvas[aria-label="Scan this QR code to link a device!"]')
        try:
            qr.wait_for(timeout=15000)
            unlogged_list.append(profile)
            log.warning("В аккаунт с номером %s НЕ вошли", profile)
        except Exception:
            logged_list.append(profile)
            log.info("В аккаунт с номером %s вошли", profile)
        context.close()
        sum_dict["unlogged"] = unlogged_list
        sum_dict['logged'] = logged_list

        log.info("Проверка номеров окончена")

    return sum_dict