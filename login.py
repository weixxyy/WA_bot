from playwright.sync_api import sync_playwright

from logger import get_logger

log = get_logger(__name__)

def do_log_in(context):
        page = context.pages[0] if context.pages else context.new_page()

        page.goto("https://web.whatsapp.com")

        log.info("WhatsApp Web открыт.")
        log.info("Если появился QR-код — отсканируй его телефоном.")

        input("После входа нажми Enter здесь...")

        log.info("Продолжаем работу бота.")