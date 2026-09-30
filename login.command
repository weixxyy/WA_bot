#!/usr/bin/env bash
# Управление аккаунтами WhatsApp в macOS: двойной клик в Finder.
#
# Тонкая обёртка над login.sh (тот же сценарий для терминала). Аргументы
# пробрасываются дальше, поэтому из Finder удобно запускать меню, а из
# терминала — конкретное действие:
#     ./login.command                  # интерактивное меню
#     ./login.command --list           # показать профили из profiles/
#     ./login.command --delete 7 --yes # удалить профиль без подтверждения
#
# Если macOS отказывается открывать файл («не удалось проверить разработчика»),
# снимите карантин и верните право на запуск:
#     xattr -d com.apple.quarantine setup.command login.command run.command
#     chmod +x setup.command login.command run.command

set -uo pipefail

cd "$(dirname "$0")" || exit 1

# Finder открывает Terminal с «чистым» окружением, в котором не видно Homebrew,
# поэтому добавляем типовые каталоги: /opt/homebrew — Apple Silicon, /usr/local — Intel.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

status=0
./login.sh "$@" || status=$?

# Окно Terminal, открытое двойным кликом, закрывается сразу после выхода из
# скрипта, поэтому ждём Enter: иначе ошибку прочитать не успеешь.
# WA_BOT_NO_PAUSE=1 отключает паузу, если обёртку зовут из другого скрипта.
if [ -t 0 ] && [ "${WA_BOT_NO_PAUSE:-0}" != "1" ]; then
    echo
    read -r -p "Нажмите Enter, чтобы закрыть окно..." _ || true
fi

exit "$status"
