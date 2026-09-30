#!/usr/bin/env bash
# Установка окружения WA_bot в macOS: двойной клик в Finder.
#
# Файл — тонкая обёртка над setup.sh: сам сценарий лежит там, чтобы одинаково
# работать и из терминала (./setup.sh), и из Finder (двойной клик по этому файлу).
# Аргументы пробрасываются дальше: ./setup.command (как и ./setup.sh) их не
# использует, обёртка нужна только для удобного запуска.
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
./setup.sh "$@" || status=$?

# Окно Terminal, открытое двойным кликом, закрывается сразу после выхода из
# скрипта, поэтому ждём Enter: иначе ни ошибку, ни итог прочитать не успеешь.
# WA_BOT_NO_PAUSE=1 отключает паузу, если обёртку зовут из другого скрипта.
if [ -t 0 ] && [ "${WA_BOT_NO_PAUSE:-0}" != "1" ]; then
    echo
    read -r -p "Нажмите Enter, чтобы закрыть окно..." _ || true
fi

exit "$status"
