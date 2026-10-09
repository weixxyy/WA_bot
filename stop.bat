@echo off
chcp 65001 >nul
rem Остановка панели WA_bot (Windows).
rem
rem Гасит панель (webapp.py) либо прогон main.py / join_groups.py, даже если окно
rem консоли уже закрыто: цель ищется по logs\wa_panel.json и logs\wa_bot.lock.
rem Нужен, когда панель запущена без своего окна (через `start`, из другого сеанса,
rem из IDE) — тогда Ctrl+C нажать негде, а процесс держит блокировку бота.
rem
rem Аргументы пробрасываются в stop.py:
rem   stop.bat              остановить панель или прогон
rem   stop.bat --dry-run    только показать, что будет остановлено
rem   stop.bat --force      не просить завершиться, а убивать сразу
rem
rem Запуск: двойной клик или stop.bat в командной строке из каталога проекта.

setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo Не найдено виртуальное окружение .venv.
    echo Сначала выполните setup.bat
    pause
    exit /b 1
)

"%VENV_PY%" stop.py %*
set "EXIT_CODE=%ERRORLEVEL%"

pause
exit /b %EXIT_CODE%
