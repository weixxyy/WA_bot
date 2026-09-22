from operator import iadd

from login_dict import login_dict
from logger import get_logger
from pathlib import Path

log = get_logger(__name__)

def login_check(playwright):
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
            login_dict[profile] = False
            log.warning("В аккаунт с номером %s НЕ вошли", profile)
        except Exception:
            login_dict[profile] = True
            log.info("В аккаунт с номером %s вошли", profile)
        context.close()

        log.info("Проверка номеров окончена")