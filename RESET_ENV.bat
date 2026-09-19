@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo This will remove only NexaClean's local .venv environment.
choice /C YN /M "Continue"
if errorlevel 2 exit /b 0
if exist ".venv" rmdir /s /q ".venv"
echo Environment removed. Run SETUP.bat to reinstall dependencies.
pause
