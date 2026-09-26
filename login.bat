@echo off
chcp 65001 >nul
rem Добавление аккаунтов WhatsApp (Windows).
rem
rem Скрипт спрашивает, сколько аккаунтов добавить и как назвать их профили,
rem затем по очереди открывает Firefox на web.whatsapp.com — нужно отсканировать
rem QR-код телефоном и нажать Enter в консоли.
rem
rem Запуск: двойной клик или login.bat в командной строке из каталога проекта.

setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo Не найдено виртуальное окружение .venv
    echo Сначала выполните setup.bat
    pause
    exit /b 1
)

"%VENV_PY%" login.py

pause
exit /b 0
