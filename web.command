#!/usr/bin/env bash
# Локальная веб-панель WA_bot в macOS: двойной клик в Finder.
#
# Тонкая обёртка над web.sh (тот же сценарий для терминала). Аргументы
# пробрасываются дальше:
#     ./web.command              # панель и автооткрытие браузера
#     ./web.command --no-browser # без автооткрытия браузера
#     ./web.command --port 9000  # другой порт
#
# Пока панель работает, окно Terminal должно оставаться открытым: закрытие окна
# останавливает панель и освобождает блокировку бота.
#
# Если macOS отказывается открывать файл («не удалось проверить разработчика»),
# снимите карантин и верните право на запуск:
#     xattr -d com.apple.quarantine setup.command login.command run.command web.command
#     chmod +x setup.command login.command run.command web.command

set -uo pipefail

cd "$(dirname "$0")" || exit 1

# Finder открывает Terminal с «чистым» окружением, в котором не видно Homebrew,
# поэтому добавляем типовые каталоги: /opt/homebrew — Apple Silicon, /usr/local — Intel.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

status=0
./web.sh "$@" || status=$?

# Окно Terminal, открытое двойным кликом, закрывается сразу после выхода из
# скрипта, поэтому ждём Enter: иначе итог прочитать не успеешь.
# WA_BOT_NO_PAUSE=1 отключает паузу, если обёртку зовут из другого скрипта.
if [ -t 0 ] && [ "${WA_BOT_NO_PAUSE:-0}" != "1" ]; then
    echo
    read -r -p "Нажмите Enter, чтобы закрыть окно..." _ || true
fi

exit "$status"
