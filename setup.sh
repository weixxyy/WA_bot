#!/usr/bin/env bash
# Self-contained bootstrap for macOS and Ubuntu Linux.
set -euo pipefail

cd "$(dirname "$0")"

UV_DIR="$PWD/.tools/uv"
UV_BIN="$UV_DIR/uv"
VENV_DIR="$PWD/.venv"
VENV_PY="$VENV_DIR/bin/python"
BOOTSTRAP_MARKER="$VENV_DIR/.wa-bot-bootstrap-hash"

if [ ! -x "$UV_BIN" ]; then
    echo "==> Скачиваем менеджер окружения uv"
    if ! command -v curl >/dev/null 2>&1; then
        echo "Ошибка: для первого запуска нужна команда curl."
        exit 1
    fi
    mkdir -p "$UV_DIR"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL="$UV_DIR" sh
fi

if [ "${WA_BOT_FORCE_REPAIR:-0}" = "1" ]; then
    echo "==> Пересоздаём окружение Python"
    "$UV_BIN" venv --clear --python 3.12 "$VENV_DIR"
elif [ ! -x "$VENV_PY" ]; then
    echo "==> Устанавливаем управляемый Python 3.12"
    "$UV_BIN" venv --python 3.12 "$VENV_DIR"
fi

if ! "$VENV_PY" --version >/dev/null 2>&1; then
    echo "==> Окружение Python повреждено, пересоздаём"
    "$UV_BIN" venv --clear --python 3.12 "$VENV_DIR"
fi

REQUIREMENTS_HASH="$("$VENV_PY" -c 'import hashlib; print(hashlib.sha256(open("requirements.txt", "rb").read()).hexdigest())')"
ENVIRONMENT_READY=0
if "$VENV_PY" -c 'from pathlib import Path; import fastapi, uvicorn; from playwright.sync_api import sync_playwright; p = sync_playwright().start(); path = p.firefox.executable_path; p.stop(); raise SystemExit(0 if Path(path).is_file() else 1)' >/dev/null 2>&1; then
    ENVIRONMENT_READY=1
fi
if [ "${WA_BOT_FORCE_REPAIR:-0}" != "1" ] && [ "$ENVIRONMENT_READY" = "1" ] && [ -f "$BOOTSTRAP_MARKER" ] && [ "$(cat "$BOOTSTRAP_MARKER")" = "$REQUIREMENTS_HASH" ]; then
    echo "==> Окружение уже готово"
    exit 0
fi

echo "==> Проверяем зависимости приложения"
"$UV_BIN" --system-certs pip install --python "$VENV_PY" -r requirements.txt

if [ "$(uname -s)" = "Linux" ] && [ ! -f "$VENV_DIR/.linux-deps-ready" ]; then
    echo "==> Устанавливаем Firefox Playwright и системные библиотеки"
    echo "    Ubuntu может запросить пароль sudo."
    "$VENV_PY" -m playwright install --with-deps firefox
    touch "$VENV_DIR/.linux-deps-ready"
else
    echo "==> Проверяем Firefox Playwright"
    "$VENV_PY" -m playwright install firefox
fi

printf '%s' "$REQUIREMENTS_HASH" > "$BOOTSTRAP_MARKER"
echo "==> Окружение готово"
