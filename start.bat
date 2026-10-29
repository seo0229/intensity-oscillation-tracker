@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please follow the setup instructions in README.md first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m tracker
if errorlevel 1 pause
