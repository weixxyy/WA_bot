#!/usr/bin/env bash
# Управление аккаунтами WhatsApp (Linux / macOS).
#
# По умолчанию скрипт показывает меню: добавить аккаунты (спрашивает, сколько и
# как назвать профили, затем по очереди открывает Firefox на web.whatsapp.com —
# нужно отсканировать QR-код телефоном и нажать Enter) или удалить профиль.
#
# Аргументы пробрасываются в login.py:
#   ./login.sh                        # интерактивное меню
#   ./login.sh --list                 # показать профили из profiles/
#   ./login.sh --delete 7             # удалить профиль profiles/7 (с подтверждением)
#   ./login.sh --delete 7 8 --yes     # без подтверждения
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

exec "$VENV_PY" login.py "$@"
