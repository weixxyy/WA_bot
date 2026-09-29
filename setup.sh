#!/usr/bin/env bash
# Установка окружения проекта WA_bot (Linux / macOS).
#
# Что делает скрипт:
#   1. создаёт виртуальное окружение .venv (если его ещё нет);
#   2. ставит зависимости из requirements.txt;
#   3. скачивает браузер Firefox для Playwright;
#   4. создаёт пустой каталог profiles/.
#
# Использование:
#   ./setup.sh
#
# Переменные окружения:
#   PYTHON_BIN=python3.13   интерпретатор для создания venv (по умолчанию python3)
#   VENV_DIR=.venv          имя каталога окружения (по умолчанию .venv)
#   INSTALL_DEPS=1          дополнительно выполнить playwright install-deps firefox
#                           (тянет системные библиотеки, обычно требует sudo)

set -euo pipefail

# Работаем из каталога проекта, а не из текущего каталога запуска.
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"

if [ ! -d "$VENV_DIR" ]; then
    echo "==> Создаём виртуальное окружение $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PY="$VENV_DIR/bin/python"

echo "==> Обновляем pip"
"$VENV_PY" -m pip install --upgrade pip

echo "==> Ставим зависимости из requirements.txt"
"$VENV_PY" -m pip install -r requirements.txt

echo "==> Скачиваем браузер Firefox для Playwright"
"$VENV_PY" -m playwright install firefox

if [ "${INSTALL_DEPS:-0}" = "1" ]; then
    echo "==> Ставим системные библиотеки для Firefox (может спросить пароль sudo)"
    "$VENV_PY" -m playwright install-deps firefox
fi

mkdir -p profiles

echo
echo "Готово."
echo "Запуск бота: $VENV_PY main.py"
