@echo off
chcp 65001 >nul
rem Управление аккаунтами WhatsApp (Windows).
rem
rem По умолчанию скрипт показывает меню: добавить аккаунты (спрашивает, сколько
rem и как назвать профили, затем по очереди открывает Firefox на
rem web.whatsapp.com — нужно отсканировать QR-код телефоном и нажать Enter)
rem или удалить профиль.
rem
rem Аргументы пробрасываются в login.py:
rem   login.bat                        интерактивное меню
rem   login.bat --list                 показать профили из profiles/
rem   login.bat --delete 7             удалить профиль profiles\7 (с подтверждением)
rem   login.bat --delete 7 8 --yes     без подтверждения
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

"%VENV_PY%" login.py %*

pause
exit /b 0
