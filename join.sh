#!/usr/bin/env bash
# Вступление в группы WhatsApp по расписанию (Linux / macOS).
#
# Только вступление: скрипт идёт по ссылкам из urls.txt, нажимает «Вступить в
# группу» / «Запрос на вступление» (а «Отменить запрос» не трогает — заявка
# сбросилась бы) и раскладывает группы по файлам
# only_admins_groups.txt / closed_groups.txt / open_groups.txt. Сообщений он не
# отправляет — для рассылки есть run.sh.
#
# По умолчанию скрипт просыпается во времена из scheduler.DEFAULT_JOIN_RUN_AT
# (10:00 и 19:00, местное время машины). Аргументы пробрасываются в
# join_groups.py:
#   ./join.sh              # по расписанию, пока не остановят (Ctrl+C)
#   ./join.sh --once       # один прогон и выход
#   ./join.sh --at 10:30 22:15
#
# Переменные окружения:
#   VENV_DIR=.venv          имя каталога окружения (по умолчанию .venv)

set -euo pipefail

# Работаем из каталога проекта, а не из текущего каталога запуска.
cd "$(dirname "$0")"

VENV_DIR="${VENV_DIR:-.venv}"
VENV_PY="$VENV_DIR/bin/python"

if [ ! -x "$VENV_PY" ]; then
    echo "Не найдено виртуальное окружение $VENV_DIR ($VENV_PY)."
    echo "Сначала выполните ./setup.sh"
    exit 1
fi

exec "$VENV_PY" join_groups.py "$@"
