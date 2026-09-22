import script, login
from playwright.sync_api import sync_playwright
from urls_list import INVITE_URLS
from login_check import login_check
from logger import get_logger, setup_logging

log = get_logger(__name__)

def main():
    setup_logging()
    log.info("Запуск бота")
    with sync_playwright() as pw:
        login_dict = login_check(pw)
        for profile in login_dict['logged']:
            context = pw.firefox.launch_persistent_context(
                user_data_dir=profile,
                headless=True,
            )
            log.info("Профиль %s: запускаем сценарий", profile)
            script.do_script(urls_list=INVITE_URLS, context=context)

            context.close()
        log.info("Бот завершил работу")
    if login_dict['unlogged']:
        log.info('В списке номеров есть незарегестрированные аккаунты\n'
                 'Запустить регистрацию?\n'
                 '1 - да 2 - нет')
    # на этом этапе пока что выкатим код, продолжим когда подключим к базам


if __name__ == "__main__":
    main()