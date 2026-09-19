@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title NexaClean Pro - Setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo Setup failed. See setup.log for details.
  pause
)
exit /b %RC%
