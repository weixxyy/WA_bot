import json
import time
from datetime import datetime
from pathlib import Path

from logger import get_logger

log = get_logger(__name__)

# Статистика по профилю лежит рядом с его Firefox-профилем.
STATS_FILE_NAME = "bot_stats.json"


def _stats_path(profile) -> Path:
    """Путь к файлу статистики внутри каталога профиля."""
    return Path(profile) / STATS_FILE_NAME


def _save_stats(profile, stats) -> None:
    """Пишет статистику профиля в bot_stats.json (UTF-8, читаемый JSON)."""
    _stats_path(profile).write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _reset_stats(profile) -> dict:
    """Создаёт bot_stats.json, если его нет, и обнуляет счётчики.

    Вызывается один раз перед работой бота с профилем. ``kicked_count`` —
    сколько групп, из которых аккаунт был выгнан, встретилось за текущий
    прогон; ``last_date_change`` — время последней записи в файл.
    """
    stats = {
        "kicked_count": 0,
        "last_date_change": datetime.now().isoformat(timespec="seconds"),
    }
    _save_stats(profile, stats)
    log.info("Статистика профиля %s обнулена: %s", profile, stats)
    return stats


def do_script(urls_list, context, profile):
        # Профиль нужен, чтобы вести статистику рядом с ним: context его не отдаёт.
        stats = _reset_stats(profile)

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
            search2 = page.get_by_test_id("confirm-popup").filter(
                visible=True,
                has_text="Вы не можете вступить в данную группу, так как вы были удалены."
            ).first
            search3 = page.get_by_test_id(
                "conversation-info-header-chat-title"
            )
            search.or_(search2).or_(search3).wait_for(timeout=None)
            if search2.is_visible():
                stats["kicked_count"] += 1
                stats["last_date_change"] = datetime.now().isoformat(
                    timespec="seconds"
                )
                _save_stats(profile, stats)
                log.info(
                    "Бот был удален из группы %s (kicked_count=%s)",
                    group_name,
                    stats["kicked_count"],
                )
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