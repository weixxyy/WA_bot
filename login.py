"""Управление аккаунтами WhatsApp: добавление и удаление профилей.

По умолчанию скрипт работает интерактивно и спрашивает, что делать: добавить
аккаунты (сколько и как назвать профили — введённый номер становится именем
каталога внутри ``profiles/``) или удалить существующий профиль. При добавлении
по очереди открывается Firefox на web.whatsapp.com — нужно отсканировать QR-код
телефоном и нажать Enter в консоли.

Разовые команды есть и в аргументах: ``--list`` и ``--delete`` (см. ``--help``).
Они браузер не открывают и ничего не добавляют.

Запуск: ``./login.sh`` (Linux / macOS) или ``login.bat`` (Windows), либо
``python login.py`` при активированном окружении.
"""

import argparse
import os
import shutil
import stat
from pathlib import Path

from playwright.sync_api import sync_playwright

from bot_lock import is_locked, owner_description
from login_check import ensure_profiles_dir
from logger import get_logger, setup_logging

log = get_logger(__name__)

SITE_URL = "https://web.whatsapp.com"

# Пункты меню. Enter в ответе — добавление: так скрипт ведёт себя как раньше,
# когда удаления в нём ещё не было.
ACTION_ADD = "1"
ACTION_DELETE = "2"
ACTION_EXIT = "0"

# Ответы, которые считаем подтверждением удаления.
YES_ANSWERS = {"y", "yes", "д", "да"}


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


def list_profiles(profiles_dir) -> list[Path]:
    """Возвращает каталоги профилей из ``profiles/``.

    Служебные файлы вроде ``.gitkeep`` и скрытые каталоги профилями не считаем.
    """
    profiles_dir = ensure_profiles_dir(profiles_dir)
    profiles = [
        path
        for path in profiles_dir.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ]
    return sorted(profiles)


def show_profiles(profiles: list[Path]) -> None:
    """Печатает нумерованный список профилей."""
    if not profiles:
        print("В каталоге профилей пока ничего нет.")
        return

    print("Доступные профили:")
    for number, profile in enumerate(profiles, start=1):
        print(f"  {number}) {profile.name}")


def parse_choice(raw: str, profiles: list[Path]) -> list[Path]:
    """Разбирает ответ с номерами и/или названиями профилей.

    Принимает номера из списка (``1 3``), имена каталогов (``7, 8``) и их смесь.
    Имя важнее номера: профиль может называться ``7``, и тогда ``7`` — это имя,
    а не седьмой пункт списка.

    :raises ValueError: если введённого профиля нет в списке.
    """
    chosen = []
    for token in raw.replace(",", " ").split():
        profile = next((item for item in profiles if item.name == token), None)
        if profile is None and token.isdigit():
            number = int(token)
            if 1 <= number <= len(profiles):
                profile = profiles[number - 1]
        if profile is None:
            raise ValueError(f"Неизвестный профиль: {token}")
        if profile not in chosen:
            chosen.append(profile)
    return chosen


def ask_profiles_to_delete(profiles: list[Path]) -> list[Path]:
    """Спрашивает, какие профили удалить. Пустой ответ — отмена."""
    while True:
        raw = input("Номера или названия профилей (Enter — отмена): ").strip()
        if not raw:
            return []
        try:
            return parse_choice(raw, profiles)
        except ValueError as error:
            print(f"{error}. Введите номер из списка или название каталога.")


def confirm_delete(profiles: list[Path]) -> bool:
    """Показывает, что именно будет удалено, и спрашивает подтверждение."""
    print()
    print("Будут удалены:")
    for profile in profiles:
        print(f"  {profile}")
    print(
        "Вместе с профилем удалится сессия WhatsApp и файл bot_stats.json — "
        "аккаунт придётся добавлять заново."
    )
    return input("Продолжить? [y/N]: ").strip().lower() in YES_ANSWERS


def clear_readonly(profile: Path) -> None:
    """Снимает флаг «только для чтения» с файлов профиля.

    На Windows Firefox оставляет часть файлов профиля доступными только для
    чтения, и ``shutil.rmtree`` на них падает. Каталоги обходим без перехода по
    симлинкам: внутри профиля есть ссылка ``lock``.
    """
    for root, dirs, files in os.walk(profile, followlinks=False):
        for name in dirs + files:
            path = Path(root) / name
            if path.is_symlink():
                # Ссылку удаляем как есть, цель (живой процесс) не трогаем.
                continue
            try:
                path.chmod(path.stat().st_mode | stat.S_IWRITE)
            except OSError:
                # Не смогли снять атрибут — пусть решает shutil.rmtree.
                continue


def delete_profile(profile: Path, profiles_dir) -> bool:
    """Удаляет каталог профиля вместе с сессией WhatsApp.

    :return: ``True`` — профиль удалён, ``False`` — удалить не удалось.
    """
    profile = Path(profile)
    profiles_dir = Path(profiles_dir)

    # Сюда можно попасть и из --delete, с именем, введённым вручную, поэтому
    # проверяем, что удаляем именно профиль внутри profiles/.
    if (
        not is_valid_name(profile.name)
        or profile.resolve().parent != profiles_dir.resolve()
    ):
        print(f"Удалять можно только профили внутри {profiles_dir}.")
        return False

    if not profile.is_dir():
        print(f"{profile} — не каталог профиля, удаление отменено.")
        return False

    clear_readonly(profile)
    try:
        shutil.rmtree(profile)
    except OSError as error:
        log.exception("Не удалось удалить профиль %s: %s", profile, error)
        print(
            f"Не удалось удалить {profile}: каталог занят — возможно, запущен "
            "Firefox или бот. Остановите бота (Ctrl+C), закройте Firefox "
            "и повторите."
        )
        return False

    log.info("Профиль %s удалён", profile)
    print(f"Профиль {profile} удалён.")
    return True


def ask_action() -> str:
    """Спрашивает, что сделать: добавить аккаунты, удалить профиль или выйти."""
    print("Управление аккаунтами WhatsApp")
    print(f"  {ACTION_ADD} — добавить аккаунты")
    print(f"  {ACTION_DELETE} — удалить профиль")
    print(f"  {ACTION_EXIT} — выйти")

    while True:
        answer = input(f"Действие [{ACTION_ADD}]: ").strip()
        if not answer:
            # Enter — добавление аккаунтов, как было до появления удаления.
            return ACTION_ADD
        if answer in {ACTION_ADD, ACTION_DELETE, ACTION_EXIT}:
            return answer
        print(f"Введите {ACTION_ADD}, {ACTION_DELETE} или {ACTION_EXIT}.")


def add_accounts(profiles_dir) -> None:
    """Интерактивное добавление аккаунтов: Firefox и QR-код по каждому профилю."""
    log.info("Добавление аккаунтов WhatsApp")
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


def delete_accounts(profiles_dir) -> None:
    """Интерактивное удаление профилей из ``profiles/``."""
    log.info("Удаление профилей WhatsApp")
    profiles = list_profiles(profiles_dir)
    show_profiles(profiles)
    if not profiles:
        return

    if is_locked():
        # Профиль может быть открыт в работающем Firefox: сначала останавливаем бота.
        owner = owner_description()
        who = f" ({owner})" if owner else ""
        log.warning("Бот сейчас работает%s: удаление отменено", who)
        print(
            f"Бот сейчас работает{who}. Остановите его (Ctrl+C) и повторите: иначе "
            "прогон может сломаться на середине, а на Windows каталог профиля "
            "вообще не удалится."
        )
        return

    chosen = ask_profiles_to_delete(profiles)
    if not chosen or not confirm_delete(chosen):
        print("Удаление отменено.")
        return

    removed = [profile for profile in chosen if delete_profile(profile, profiles_dir)]
    print(f"Удалено профилей: {len(removed)} из {len(chosen)}.")


def delete_named(names, assume_yes=False, force=False) -> None:
    """Удаляет профили, переданные в ``--delete``.

    :param assume_yes: не спрашивать подтверждение (``--yes``).
    :param force: удалять, даже если бот сейчас работает (``--force``).
    """
    profiles_dir = ensure_profiles_dir()

    if is_locked() and not force:
        owner = owner_description()
        who = f" ({owner})" if owner else ""
        log.error(
            "Бот сейчас работает%s: удаление отменено (--force обойдёт проверку)",
            who,
        )
        raise SystemExit(1)

    profiles = []
    for name in names:
        profile = profiles_dir / name
        if not is_valid_name(name) or not profile.is_dir():
            log.error("Профиль %s не найден в %s", name, profiles_dir)
            raise SystemExit(1)
        profiles.append(profile)

    if not assume_yes and not confirm_delete(profiles):
        print("Удаление отменено.")
        return

    if [profile for profile in profiles if not delete_profile(profile, profiles_dir)]:
        raise SystemExit(1)


def parse_args(argv=None):
    """Разбирает аргументы командной строки.

    Без аргументов скрипт работает как раньше — показывает интерактивное меню.
    """
    parser = argparse.ArgumentParser(
        description="Управление аккаунтами WhatsApp: добавление и удаление профилей.",
    )
    parser.add_argument(
        "--list",
        dest="list_only",
        action="store_true",
        help="показать профили из profiles/ и выйти",
    )
    parser.add_argument(
        "--delete",
        nargs="+",
        metavar="ИМЯ",
        help="удалить профили с указанными именами (каталоги внутри profiles/)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="не спрашивать подтверждение (только вместе с --delete)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="удалять, даже если бот сейчас работает (только с --delete)",
    )
    args = parser.parse_args(argv)

    if args.list_only and args.delete:
        parser.error("--list и --delete несовместимы")
    if (args.yes or args.force) and not args.delete:
        parser.error("--yes и --force имеют смысл только вместе с --delete")
    return args


def main(argv=None):
    """Точка входа: интерактивное меню либо разовая команда из аргументов."""
    setup_logging()

    args = parse_args(argv)

    if args.list_only:
        show_profiles(list_profiles(ensure_profiles_dir()))
        return

    if args.delete:
        delete_named(args.delete, assume_yes=args.yes, force=args.force)
        return

    # У человека, только что скачавшего проект, каталога profiles/ ещё нет.
    profiles_dir = ensure_profiles_dir()
    log.info("Каталог профилей: %s", profiles_dir)

    action = ask_action()
    if action == ACTION_EXIT:
        return
    if action == ACTION_DELETE:
        delete_accounts(profiles_dir)
        return
    add_accounts(profiles_dir)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print()
        print("Работа с аккаунтами прервана пользователем.")