import script, login
from playwright.sync_api import sync_playwright
from urls_list import INVITE_URLS
from login_dict import login_dict
from login_check import login_check
from logger import get_logger, setup_logging

log = get_logger(__name__)

def main():
    setup_logging()
    log.info("Запуск бота")
    with sync_playwright() as pw:
        login_check(pw)
        for profile in login_dict:
            context = pw.firefox.launch_persistent_context(
                user_data_dir=profile,
                headless=True,
            )
            if login_dict[profile]:
                log.info("Профиль %s: запускаем сценарий", profile)
                script.do_script(urls_list=INVITE_URLS, context=context)
            else:
                log.warning("Профиль %s: аккаунт не авторизован, сценарий пропущен", profile)
            context.close()
        log.info("Бот завершил работу")

if __name__ == "__main__":
    main()