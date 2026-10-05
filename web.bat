@echo off
chcp 65001 >nul
rem Локальная веб-панель WA_bot (Windows).
rem
rem Панель поднимается на этом же компьютере (http://127.0.0.1:8765) и позволяет
rem править текст сообщения, ссылки, расписание и список номеров. Пока панель
rem работает, она держит блокировку бота, поэтому параллельно запускать main.py,
rem run.bat или login.bat не нужно: второй процесс откажется стартовать.
rem
rem Аргументы пробрасываются в webapp.py:
rem   web.bat                  панель и автооткрытие браузера
rem   web.bat --no-browser     без автооткрытия браузера
rem   web.bat --port 9000      другой порт
rem
rem Запуск: двойной клик или web.bat в командной строке из каталога проекта.
rem Закрытие окна консоли останавливает панель и освобождает блокировку.

setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo Не найдено виртуальное окружение .venv.
    echo Сначала выполните setup.bat
    pause
    exit /b 1
)

"%VENV_PY%" webapp.py %*
set "EXIT_CODE=%ERRORLEVEL%"

pause
exit /b %EXIT_CODE%
