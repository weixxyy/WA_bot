import time
from pathlib import Path
from logger import get_logger

log = get_logger(__name__)


def do_script(urls_list, context):
        for invite_url in urls_list:
            page = context.new_page()

            for p in context.pages[:-1]:
                p.close()

            page.goto(
                invite_url,
                wait_until="domcontentloaded",
            )

            log.info("Ожидаем загрузку WhatsApp по ссылке %s", invite_url)

            group_name = page.locator("h3").inner_text()

            log.info("Имя группы: %s", group_name)

            search = page.get_by_role(
                "link",
                name="Continue to WhatsApp Web"
            ).first

            search.wait_for()

            log.info("Ожидаем переход в группу %s", group_name)

            with context.expect_page() as new_page_info:
                search.click()

            log.info("Переходим по ссылке-приглашению")

            page = new_page_info.value

            search = page.get_by_role(
                role="button",
                name='Вступить в группу'
            )
            search2 = page.get_by_test_id("confirm-popup").filter(has_text="Вы не можете вступить в данную группу, так как вы были удалены.")
            search3 = page.get_by_test_id(
                "conversation-info-header-chat-title"
            )
            search.or_(search2).or_(search3).wait_for(timeout=None)
            if search2.is_visible():
                log.info("Бот был удален из группы %s", group_name)
                continue
            elif search.is_visible():
                log.info("Бот вступил в группу %s", group_name)
                search.click()
            else:
                log.info("Бот уже находится в группе %s", group_name)

            message_container = page.locator('[contenteditable="true"]')
            message_container.wait_for()
            message_container.fill("ping")
            message_container.press("Enter")

            log.info("Сообщение отправлено в группу %s", group_name)

            log.info("-- Следующий чат --")

            time.sleep(3)