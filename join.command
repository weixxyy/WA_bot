#!/usr/bin/env bash
# Вступление в группы WhatsApp по расписанию в macOS: двойной клик в Finder.
#
# Тонкая обёртка над join.sh (тот же сценарий для терминала). Аргументы
# пробрасываются дальше:
#     ./join.command              # по расписанию (10:00 и 19:00), пока не остановят
#     ./join.command --once       # один прогон и выход
#     ./join.command --at 10:30   # своё время запуска
#
# Пока идёт расписание, окно Terminal должно оставаться открытым: закрытие окна
# убивает скрипт. Останавливают его штатно — Ctrl+C (окно после этого не
# закрывается само, чтобы можно было прочитать итог).
#
# Если macOS отказывается открывать файл («не удалось проверить разработчика»),
# снимите карантин и верните право на запуск:
#     xattr -d com.apple.quarantine setup.command login.command run.command join.command
#     chmod +x setup.command login.command run.command join.command

set -uo pipefail

cd "$(dirname "$0")" || exit 1

# Finder открывает Terminal с «чистым» окружением, в котором не видно Homebrew,
# поэтому добавляем типовые каталоги: /opt/homebrew — Apple Silicon, /usr/local — Intel.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

status=0
./join.sh "$@" || status=$?

# Окно Terminal, открытое двойным кликом, закрывается сразу после выхода из
# скрипта, поэтому ждём Enter: иначе ошибку прочитать не успеешь.
# WA_BOT_NO_PAUSE=1 отключает паузу, если обёртку зовут из другого скрипта.
if [ -t 0 ] && [ "${WA_BOT_NO_PAUSE:-0}" != "1" ]; then
    echo
    read -r -p "Нажмите Enter, чтобы закрыть окно..." _ || true
fi

exit "$status"
