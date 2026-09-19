@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist "NexaClean_Pro.py" (
  call "%~dp0SETUP.bat"
  exit /b %ERRORLEVEL%
)
if not exist ".venv\Scripts\pythonw.exe" (
  echo NexaClean is not installed yet. Starting setup...
  call "%~dp0SETUP.bat"
  exit /b %ERRORLEVEL%
)
start "NexaClean Pro" ".venv\Scripts\pythonw.exe" "%~dp0NexaClean_Pro.py"
exit /b 0
