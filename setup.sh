#!/usr/bin/env bash
# Установка окружения проекта WA_bot (Linux / macOS).
#
# Что делает скрипт:
#   1. проверяет, что найден Python 3.10+ (в macOS python3 из Xcode Tools — 3.9);
#   2. создаёт виртуальное окружение .venv (если его ещё нет);
#   3. доставляет в окружение pip (ensurepip, а если его нет — get-pip.py);
#   4. ставит зависимости из requirements.txt;
#   5. скачивает браузер Firefox для Playwright;
#   6. создаёт пустой каталог profiles/.
#
# Использование:
#   ./setup.sh
#
# В macOS тот же сценарий удобно запускать двойным кликом: ./setup.command
# (обёртка добавляет в PATH каталоги Homebrew и не даёт окну Terminal закрыться).
#
# Переменные окружения:
#   PYTHON_BIN=python3.13   интерпретатор для создания venv (по умолчанию python3)
#   VENV_DIR=.venv          имя каталога окружения (по умолчанию .venv)
#   INSTALL_DEPS=1          дополнительно выполнить playwright install-deps firefox
#                           (только Linux: тянет системные библиотеки и обычно
#                           требует sudo; в macOS шаг не нужен и пропускается)
#   GET_PIP_URL=...         откуда скачать get-pip.py, если в Python нет ensurepip
#                           (по умолчанию https://bootstrap.pypa.io/get-pip.py)
#   OS_NAME=Darwin          переопределить определение ОС (нужно только для тестов)

set -euo pipefail

# Работаем из каталога проекта, а не из текущего каталога запуска.
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"
# uname -s даёт Darwin в macOS и Linux в Linux. Переменную можно задать снаружи
# (OS_NAME=Darwin ./setup.sh) — так macOS-ветки проверяются на любой машине.
OS_NAME="${OS_NAME:-$(uname -s)}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "Не найден интерпретатор $PYTHON_BIN." >&2
    if [ "$OS_NAME" = "Darwin" ]; then
        echo "Поставьте Python 3.10+ одним из способов:" >&2
        echo "    xcode-select --install          # системный Python (обычно 3.9)" >&2
        echo "    brew install python@3.13        # свежий Python из Homebrew" >&2
        echo "Либо укажите путь явно: PYTHON_BIN=/opt/homebrew/bin/python3 ./setup.sh" >&2
    else
        echo "Поставьте Python 3.10+ (Debian/Ubuntu: sudo apt install python3 python3-venv)." >&2
    fi
    exit 1
fi

# Код проекта рассчитан на 3.10+, а Playwright — на 3.9+. Проверяем версию сразу:
# иначе несовместимость вылезет где-то в недрах pip невнятной ошибкой.
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    echo "Нужен Python 3.10 или новее, а $PYTHON_BIN — это $("$PYTHON_BIN" -V 2>&1)." >&2
    if [ "$OS_NAME" = "Darwin" ]; then
        echo "В macOS python3 из Xcode Command Line Tools — это 3.9." >&2
        echo "Поставьте свежий Python и запустите установку с ним:" >&2
        echo "    brew install python@3.13" >&2
        echo "    PYTHON_BIN=python3.13 ./setup.sh" >&2
    fi
    exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "==> Создаём виртуальное окружение $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PY="$VENV_DIR/bin/python"
GET_PIP_URL="${GET_PIP_URL:-https://bootstrap.pypa.io/get-pip.py}"

# В Debian/Ubuntu модуль ensurepip лежит в отдельном пакете (python3-venv), и
# без него `python -m venv` не падает, а просто создаёт окружение без pip —
# дальше любые команды `python -m pip ...` рушатся с "No module named pip".
# Поэтому pip доставляем сами: сначала пробуем штатный ensurepip, затем
# скачиваем get-pip.py (curl -> wget -> urllib).
download_get_pip() {
    local target="$1"
    # Ошибки скачивания глушим: своё понятное сообщение печатает install_pip,
    # а трейсбек urllib/curl в консоли только путает.
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$GET_PIP_URL" -o "$target" 2>/dev/null && return 0
    fi
    if command -v wget >/dev/null 2>&1; then
        wget -qO "$target" "$GET_PIP_URL" 2>/dev/null && return 0
    fi
    "$PYTHON_BIN" -c \
        'import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' \
        "$GET_PIP_URL" "$target" 2>/dev/null
}

install_pip() {
    echo "==> В окружении нет pip, устанавливаем его"
    if "$VENV_PY" -m ensurepip --upgrade --default-pip >/dev/null 2>&1; then
        return 0
    fi

    local tmp
    # Шаблон заканчивается на X: так его понимают и GNU mktemp, и BSD-версия из
    # macOS (там суффикс после XXXXXX не допускается — выходит "Invalid argument").
    # Расширение .py не нужно: интерпретатору важен путь, а не имя файла.
    tmp="$(mktemp "${TMPDIR:-/tmp}/get-pip-XXXXXX")"
    echo "==> ensurepip недоступен, скачиваем pip: $GET_PIP_URL"
    if download_get_pip "$tmp" && "$VENV_PY" "$tmp"; then
        rm -f "$tmp"
        return 0
    fi
    rm -f "$tmp"

    echo "Не удалось установить pip в $VENV_DIR." >&2
    echo "Скачать pip по адресу $GET_PIP_URL не получилось," >&2
    echo "и модуля ensurepip в этом Python тоже нет." >&2
    echo "Проверьте доступ в сеть или поставьте модуль ensurepip:" >&2
    if [ "$OS_NAME" = "Darwin" ]; then
        echo "    brew install python@3.13        # сборка Python из Homebrew" >&2
    else
        echo "    sudo apt install python3-venv   # Debian/Ubuntu" >&2
    fi
    echo "Затем удалите каталог $VENV_DIR и запустите ./setup.sh заново." >&2
    exit 1
}

if ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    install_pip
fi

# Обновление pip необязательно: рабочая версия уже есть, а без сети
# зависимости всё равно не установятся — не срываем установку из-за этого.
echo "==> Обновляем pip"
"$VENV_PY" -m pip install --upgrade pip \
    || echo "Предупреждение: обновить pip не удалось, продолжаем."

echo "==> Ставим зависимости из requirements.txt"
"$VENV_PY" -m pip install -r requirements.txt

echo "==> Скачиваем браузер Firefox для Playwright"
"$VENV_PY" -m playwright install firefox

if [ "${INSTALL_DEPS:-0}" = "1" ]; then
    if [ "$OS_NAME" = "Darwin" ]; then
        # install-deps ставит библиотеки пакетным менеджером Linux (apt/dnf).
        # В macOS нужные библиотеки уже в системе, поэтому шаг пропускаем,
        # а не роняем установку непонятной ошибкой.
        echo "==> macOS: playwright install-deps не нужен, пропускаем"
    else
        echo "==> Ставим системные библиотеки для Firefox (может спросить пароль sudo)"
        "$VENV_PY" -m playwright install-deps firefox
    fi
fi

mkdir -p profiles

echo
echo "Готово."
echo "Запуск бота: $VENV_PY main.py"
