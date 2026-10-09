#!/usr/bin/env bash
# Остановка панели WA_bot в macOS: двойной клик в Finder.
#
# Тонкая обёртка над stop.sh (тот же сценарий для терминала). Аргументы
# пробрасываются дальше:
#     ./stop.command              # остановить панель или прогон
#     ./stop.command --dry-run    # только показать, что будет остановлено
#     ./stop.command --force      # не просить завершиться, а убивать сразу
#
# Пригодится, если панель запущена без своего окна Terminal: Ctrl+C нажать негде,
# а останавливать её всё равно нужно.
#
# Если macOS отказывается открывать файл («не удалось проверить разработчика»),
# снимите карантин и верните право на запуск:
#     xattr -d com.apple.quarantine stop.command
#     chmod +x stop.command

set -uo pipefail

cd "$(dirname "$0")" || exit 1

# Finder открывает Terminal с «чистым» окружением, в котором не видно Homebrew,
# поэтому добавляем типовые каталоги: /opt/homebrew — Apple Silicon, /usr/local — Intel.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

status=0
./stop.sh "$@" || status=$?

# Окно Terminal, открытое двойным кликом, закрывается сразу после выхода из
# скрипта, поэтому ждём Enter: иначе итог прочитать не успеешь.
# WA_BOT_NO_PAUSE=1 отключает паузу, если обёртку зовут из другого скрипта.
if [ -t 0 ] && [ "${WA_BOT_NO_PAUSE:-0}" != "1" ]; then
    echo
    read -r -p "Нажмите Enter, чтобы закрыть окно..." _ || true
fi

exit "$status"
