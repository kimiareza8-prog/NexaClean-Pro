@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title NexaClean Pro
set "APP=%~dp0_NexaClean"
set "PY=%APP%\.venv\Scripts\pythonw.exe"
set "PYC=%APP%\.venv\Scripts\python.exe"
if exist "%PY%" if exist "%APP%\NexaClean_Pro.py" (
    start "" "%PY%" "%APP%\NexaClean_Pro.py"
    exit /b 0
)
if exist "%PYC%" if exist "%APP%\NexaClean_Pro.py" (
    start "" "%PYC%" "%APP%\NexaClean_Pro.py"
    exit /b 0
)
echo First run: NexaClean Pro will install its required components automatically.
echo This may take a few minutes and only happens once.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%APP%\oneclick_setup.ps1"
exit /b %ERRORLEVEL%
