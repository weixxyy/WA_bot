@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0repair.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %EXIT_CODE%
