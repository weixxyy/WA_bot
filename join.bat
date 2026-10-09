@echo off
chcp 65001 >nul
rem Вступление в группы WhatsApp по расписанию (Windows).
rem
rem Только вступление: скрипт идёт по ссылкам из urls.txt, нажимает «Вступить в
rem группу» / «Запрос на вступление» (а «Отменить запрос» не трогает — заявка
rem сбросилась бы) и раскладывает группы по файлам
rem only_admins_groups.txt / closed_groups.txt / open_groups.txt. Сообщений он
rem не отправляет — для рассылки есть run.bat.
rem
rem По умолчанию скрипт просыпается во времена из scheduler.DEFAULT_JOIN_RUN_AT
rem (10:00 и 19:00, местное время машины). Аргументы пробрасываются в
rem join_groups.py:
rem   join.bat                   по расписанию, пока не остановят (Ctrl+C)
rem   join.bat --once            один прогон и выход
rem   join.bat --at 10:30 22:15
rem
rem Запуск: двойной клик или join.bat в командной строке из каталога проекта.

setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo Не найдено виртуальное окружение .venv.
    echo Сначала выполните setup.bat
    pause
    exit /b 1
)

"%VENV_PY%" join_groups.py %*
set "EXIT_CODE=%ERRORLEVEL%"

pause
exit /b %EXIT_CODE%
