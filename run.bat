@echo off
chcp 65001 >nul
rem Запуск бота WhatsApp по расписанию (Windows).
rem
rem По умолчанию бот просыпается во времена из scheduler.DEFAULT_RUN_AT
rem (09:00 и 18:00, местное время машины). Аргументы пробрасываются в main.py:
rem   run.bat                   по расписанию, пока не остановят (Ctrl+C)
rem   run.bat --once            один прогон и выход
rem   run.bat --at 10:30 22:15
rem
rem Запуск: двойной клик или run.bat в командной строке из каталога проекта.

setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo Не найдено виртуальное окружение .venv.
    echo Сначала выполните setup.bat
    pause
    exit /b 1
)

"%VENV_PY%" main.py %*
set "EXIT_CODE=%ERRORLEVEL%"

pause
exit /b %EXIT_CODE%
