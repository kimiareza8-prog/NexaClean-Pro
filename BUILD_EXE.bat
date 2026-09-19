@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  call "%~dp0SETUP.bat"
  if errorlevel 1 exit /b %ERRORLEVEL%
)
".venv\Scripts\python.exe" -m pip install --upgrade pyinstaller
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name "NexaClean-Pro" "NexaClean_Pro.py"
if errorlevel 1 goto :fail
echo.
echo Build complete: dist\NexaClean-Pro.exe
pause
exit /b 0
:fail
echo.
echo Build failed.
pause
exit /b 1
