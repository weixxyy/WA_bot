import script, login
from playwright.sync_api import sync_playwright
from urls_list import INVITE_URLS
from login_check import ensure_profiles_dir, login_check
from logger import get_logger, setup_logging

log = get_logger(__name__)

def main():
    setup_logging()
    log.info("Запуск бота")
    # У человека, только что скачавшего проект, каталога profiles/ ещё нет.
    profiles_dir = ensure_profiles_dir()
    log.info("Каталог профилей: %s", profiles_dir)
    with sync_playwright() as pw:
        login_dict = login_check(pw)
        if not login_dict['logged']:
            log.warning(
                "В %s нет ни одного авторизованного профиля.\n"
                "Положите в него каталог с уже выполненным входом в WhatsApp Web "
                "и запустите бота снова.",
                profiles_dir,
            )
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