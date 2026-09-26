"""Интерактивное добавление аккаунтов WhatsApp.

Скрипт спрашивает, сколько аккаунтов добавить и как назвать их профили
(введённый номер становится именем каталога внутри ``profiles/``), после чего
по очереди открывает Firefox на web.whatsapp.com — нужно отсканировать QR-код
телефоном и нажать Enter в консоли.

Запуск: ``./login.sh`` (Linux / macOS) или ``login.bat`` (Windows), либо
``python login.py`` при активированном окружении.
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

from login_check import ensure_profiles_dir
from logger import get_logger, setup_logging

log = get_logger(__name__)

SITE_URL = "https://web.whatsapp.com"


def ask_count() -> int:
    """Спрашивает, сколько аккаунтов добавить.

    :return: целое число больше нуля; на мусорный ввод переспрашивает.
    """
    while True:
        raw = input("Сколько аккаунтов хотите добавить? ").strip()
        try:
            count = int(raw)
        except ValueError:
            print("Нужно целое число, например 1.")
            continue
        if count <= 0:
            print("Число должно быть больше нуля.")
            continue
        return count


def is_valid_name(name: str) -> bool:
    """Проверяет, что из имени получится безопасный каталог профиля."""
    if not name:
        return False
    if name in {".", "..", ".gitkeep"}:
        return False
    # Имя каталога не должно содержать разделителей пути.
    if "/" in name or "\\" in name:
        return False
    return True


def ask_name(number: int, profiles_dir, taken: list) -> str:
    """Спрашивает название (номер) профиля № ``number``.

    :param taken: уже занятые в этом запуске имена профилей.
    :return: корректное имя, для которого каталога ещё нет.
    """
    while True:
        name = input(f"Название (номер) профиля №{number}: ").strip()
        if not is_valid_name(name):
            print("Недопустимое название: пусто или содержит / \\.")
            continue
        if name in taken:
            print(f"Профиль {name} уже добавлен в этом запуске, введите другой номер.")
            continue
        profile = Path(profiles_dir) / name
        if profile.exists():
            print(f"Каталог {profile} уже существует — существующую сессию не трогаем.")
            continue
        return name


def do_log_in(context):
    """Открывает WhatsApp Web и ждёт, пока пользователь выполнит вход.

    Браузер должен быть запущен не в headless-режиме, иначе QR-код не виден.
    """
    page = context.pages[0] if context.pages else context.new_page()

    page.goto(SITE_URL)

    log.info("WhatsApp Web открыт.")
    log.info("Если появился QR-код — отсканируй его телефоном.")

    input("После входа нажми Enter здесь...")

    log.info("Продолжаем работу бота.")


def main():
    setup_logging()
    log.info("Добавление аккаунтов WhatsApp")
    # У человека, только что скачавшего проект, каталога profiles/ ещё нет.
    profiles_dir = ensure_profiles_dir()
    log.info("Каталог профилей: %s", profiles_dir)

    count = ask_count()

    names = []
    for number in range(1, count + 1):
        names.append(ask_name(number, profiles_dir, names))
    log.info("Будут добавлены профили: %s", ", ".join(names))

    # Создаём каталоги заранее: Playwright тоже умеет это делать, но так профили
    # появляются сразу, ещё до открытия браузера.
    profiles = []
    for name in names:
        profile = profiles_dir / name
        profile.mkdir(parents=True, exist_ok=True)
        profiles.append(profile)

    # headless=False обязателен: QR-код нужно видеть, чтобы отсканировать его.
    with sync_playwright() as playwright:
        for index, profile in enumerate(profiles, start=1):
            log.info(
                "Профиль %s (%s из %s): открываем WhatsApp Web",
                profile,
                index,
                len(profiles),
            )
            context = playwright.firefox.launch_persistent_context(
                user_data_dir=profile,
                headless=False,
            )
            try:
                do_log_in(context)
            finally:
                # Браузер закрываем всегда, даже если вход прервали.
                context.close()

    log.info("Готово: добавлено профилей — %s", len(profiles))


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print()
        print("Добавление аккаунтов прервано пользователем.")