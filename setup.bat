@echo off
chcp 65001 >nul
rem Установка окружения проекта WA_bot (Windows).
rem
rem Что делает скрипт:
rem   1. создаёт виртуальное окружение .venv (если его ещё нет);
rem   2. доставляет в окружение pip (ensurepip, а если его нет — get-pip.py);
rem   3. ставит зависимости из requirements.txt;
rem   4. скачивает браузер Firefox для Playwright;
rem   5. создаёт пустой каталог profiles.
rem
rem Переменная окружения:
rem   GET_PIP_URL   откуда скачать get-pip.py, если в Python нет ensurepip
rem                 (по умолчанию https://bootstrap.pypa.io/get-pip.py)
rem
rem Запуск: двойной клик или setup.bat в командной строке из каталога проекта.

setlocal
cd /d "%~dp0"

set "PYTHON_BIN=python"
where py >nul 2>nul && set "PYTHON_BIN=py -3"
if not defined GET_PIP_URL set "GET_PIP_URL=https://bootstrap.pypa.io/get-pip.py"

if not exist ".venv" (
    echo ==^> Создаём виртуальное окружение .venv
    %PYTHON_BIN% -m venv .venv || goto :error
)

set "VENV_PY=.venv\Scripts\python.exe"

rem В сборках с python.org pip ставится при создании venv; если его нет (сборка
rem без ensurepip), доставляем pip сами: ensurepip, затем get-pip.py.
"%VENV_PY%" -m pip --version >nul 2>nul
if not errorlevel 1 goto :pip_ready

echo ==^> В окружении нет pip, устанавливаем его
"%VENV_PY%" -m ensurepip --upgrade --default-pip >nul 2>nul
if not errorlevel 1 goto :pip_check

echo ==^> ensurepip недоступен, скачиваем pip: %GET_PIP_URL%
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -UseBasicParsing -Uri '%GET_PIP_URL%' -OutFile '%TEMP%\get-pip.py'" || goto :error
"%VENV_PY%" "%TEMP%\get-pip.py" || goto :error
del "%TEMP%\get-pip.py" >nul 2>nul

:pip_check
"%VENV_PY%" -m pip --version >nul 2>nul
if not errorlevel 1 goto :pip_ready

echo.
echo Не удалось установить pip в .venv. Проверьте доступ в сеть или
echo переустановите Python с официального сайта python.org.
goto :error

:pip_ready
rem Обновление pip необязательно: рабочая версия уже есть, а без сети
rem зависимости всё равно не установятся — не срываем установку из-за этого.
echo ==^> Обновляем pip
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 echo Предупреждение: обновить pip не удалось, продолжаем.

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
