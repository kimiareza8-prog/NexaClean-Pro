@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_as_admin.ps1"
if errorlevel 1 pause
exit /b %ERRORLEVEL%
