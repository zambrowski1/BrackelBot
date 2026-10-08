@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv
  if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -e . --no-deps
if errorlevel 1 goto fail
".venv\Scripts\python.exe" run_gui.py
if errorlevel 1 goto fail
exit /b 0
:fail
echo ERROR: Installation or startup failed. Check Python and network.
pause
exit /b 1
