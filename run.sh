#!/usr/bin/env bash
# Запуск бота WhatsApp по расписанию (Linux / macOS).
#
# По умолчанию бот просыпается во времена из scheduler.DEFAULT_RUN_AT
# (09:00 и 18:00, местное время машины). Аргументы пробрасываются в main.py:
#   ./run.sh              # по расписанию, пока не остановят (Ctrl+C)
#   ./run.sh --once       # один прогон и выход
#   ./run.sh --at 10:30 22:15
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

exec "$VENV_PY" main.py "$@"
