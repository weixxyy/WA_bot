#!/usr/bin/env bash
# Добавление аккаунтов WhatsApp (Linux / macOS).
#
# Скрипт спрашивает, сколько аккаунтов добавить и как назвать их профили,
# затем по очереди открывает Firefox на web.whatsapp.com — нужно отсканировать
# QR-код телефоном и нажать Enter в консоли.
#
# Использование:
#   ./login.sh
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

exec "$VENV_PY" login.py
