@echo off
chcp 65001 >nul
rem Установка окружения проекта WA_bot (Windows).
rem
rem Что делает скрипт:
rem   1. создаёт виртуальное окружение .venv (если его ещё нет);
rem   2. ставит зависимости из requirements.txt;
rem   3. скачивает браузер Firefox для Playwright;
rem   4. создаёт пустой каталог profiles.
rem
rem Запуск: двойной клик или setup.bat в командной строке из каталога проекта.

setlocal
cd /d "%~dp0"

set "PYTHON_BIN=python"
where py >nul 2>nul && set "PYTHON_BIN=py -3"

if not exist ".venv" (
    echo ==^> Создаём виртуальное окружение .venv
    %PYTHON_BIN% -m venv .venv || goto :error
)

set "VENV_PY=.venv\Scripts\python.exe"

echo ==^> Обновляем pip
"%VENV_PY%" -m pip install --upgrade pip || goto :error

echo ==^> Ставим зависимости из requirements.txt
"%VENV_PY%" -m pip install -r requirements.txt || goto :error

echo ==^> Скачиваем браузер Firefox для Playwright
"%VENV_PY%" -m playwright install firefox || goto :error

if not exist "profiles" mkdir profiles

echo.
echo Готово.
echo Запуск бота: "%VENV_PY%" main.py
pause
exit /b 0

:error
echo.
echo Установка завершилась с ошибкой, смотрите сообщения выше.
pause
exit /b 1
