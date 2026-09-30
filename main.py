import argparse

import script
from playwright.sync_api import sync_playwright

from bot_lock import LOCK_FILE, owner_description, run_lock
from urls_list import URLS_FILE, load_invite_urls, load_message
from login_check import ensure_profiles_dir, login_check
from logger import get_logger, setup_logging
from scheduler import DEFAULT_RUN_AT, parse_run_at, run_forever

log = get_logger(__name__)


def run_once() -> dict:
    """Один полный прогон бота: проверка профилей и сценарий по каждой группе.

    Ссылки и текст сообщения читаются из ``urls.txt`` / ``send_to.txt`` в начале
    прогона. Если ссылок нет, прогон пропускается (браузеры не поднимаются).

    :return: результат :func:`login_check` со списками ``logged``/``unlogged``.
    """
    # У человека, только что скачавшего проект, каталога profiles/ ещё нет.
    profiles_dir = ensure_profiles_dir()
    log.info("Каталог профилей: %s", profiles_dir)
    # Конфигурацию читаем до старта браузера: файлы перечитываются каждый прогон,
    # поэтому правки urls.txt и send_to.txt подхватываются без перезапуска бота.
    urls = load_invite_urls()
    message = load_message()
    log.info("Ссылок в %s: %s", URLS_FILE, len(urls))
    if not urls:
        log.error("В %s нет ни одной ссылки — прогон пропущен.", URLS_FILE)
        return {"logged": [], "unlogged": []}
    # имя профиля -> сколько групп, из которых его выгнали за этот прогон
    # (None, если статистику профиля прочитать не удалось).
    run_stats = {}
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
            try:
                log.info("Профиль %s: запускаем сценарий", profile)
                script.do_script(
                    urls_list=urls,
                    context=context,
                    profile=profile,
                    message=message,
                )
            except Exception:
                # Сбой на одном профиле не должен прерывать обход остальных.
                log.exception("Профиль %s: сценарий прерван ошибкой", profile)
            finally:
                # Итог печатаем и при ошибке: bot_stats.json уже на диске.
                run_stats[profile.name] = script.log_stats(profile)
                context.close()

    if run_stats:
        summary = script.save_run_summary(run_stats)
        log.info(
            "Сводка прогона: выгнан из %s групп(ы) суммарно по %s профилям "
            "(файл %s)",
            summary["total_kicked"],
            len(run_stats),
            script.SUMMARY_FILE,
        )

    if login_dict['unlogged']:
        log.info('В списке номеров есть незарегестрированные аккаунты\n'
                 'Запустить регистрацию?\n'
                 '1 - да 2 - нет')
    # на этом этапе пока что выкатим код, продолжим когда подключим к базам
    return login_dict


def parse_args(argv=None):
    """Разбирает аргументы командной строки.

    ``--once`` — один прогон и выход, ``--at ЧЧ:ММ ...`` — свои времена запусков
    вместо :data:`scheduler.DEFAULT_RUN_AT`.
    """
    parser = argparse.ArgumentParser(
        description="Бот WhatsApp: заходит по invite-ссылкам и отправляет сообщение.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="выполнить один прогон и выйти (без расписания)",
    )
    parser.add_argument(
        "--at",
        nargs="+",
        metavar="ЧЧ:ММ",
        default=None,
        help=(
            "времена запусков в местном времени, например: --at 09:00 18:00 "
            f"(по умолчанию {' '.join(DEFAULT_RUN_AT)})"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "запустить, даже если бот уже работает "
            "(второй экземпляр без этого флага не стартует)"
        ),
    )
    args = parser.parse_args(argv)

    if args.once and args.at:
        parser.error("--once и --at несовместимы: --once отключает расписание")
    if args.at:
        # Проверяем формат сразу: опечатка в --at не должна ждать первого слота.
        try:
            args.at = parse_run_at(args.at)
        except ValueError as error:
            parser.error(str(error))
    return args


def run_bot(args) -> None:
    """Выполняет режим из аргументов: один прогон либо работу по расписанию."""
    if args.once:
        log.info("Режим: один прогон")
        run_once()
        log.info("Бот завершил работу")
        return

    log.info("Режим: по расписанию")
    run_forever(run_once, args.at or DEFAULT_RUN_AT)


def main(argv=None):
    """Точка входа.

    Без флагов бот работает по расписанию (:data:`scheduler.DEFAULT_RUN_AT`),
    ``--once`` выполняет один прогон и завершает работу. Пока бот работает, он
    держит блокировку :data:`bot_lock.LOCK_FILE`, поэтому второй экземпляр без
    ``--force`` не стартует: два прогона по одним профилям ломают Firefox-профиль.
    """
    setup_logging()
    args = parse_args(argv)
    log.info("Запуск бота")

    if args.force:
        log.warning("--force: блокировку %s не проверяю", LOCK_FILE)
        run_bot(args)
        return

    with run_lock() as acquired:
        if not acquired:
            owner = owner_description()
            log.error(
                "Бот уже запущен%s. Второй экземпляр не стартует: два прогона по "
                "одним профилям ломают Firefox-профиль. Остановите работающий бот "
                "(Ctrl+C) и повторите либо запустите с --force.",
                f" ({owner})" if owner else "",
            )
            raise SystemExit(1)
        run_bot(args)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        # Остановить планировщик можно через Ctrl+C — это штатный выход.
        log.info("Бот остановлен пользователем")