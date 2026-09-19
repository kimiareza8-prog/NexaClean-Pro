@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  call "%~dp0SETUP.bat"
  exit /b %ERRORLEVEL%
)
".venv\Scripts\python.exe" "%~dp0NexaClean_Pro.py"
echo.
echo Process exited with code %ERRORLEVEL%.
pause
