@echo off
setlocal
cd /d "%~dp0"
if exist "WikipediaStatsUpdater.exe" (
  start "" "%~dp0WikipediaStatsUpdater.exe"
  exit /b 0
)
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto failure
)
.venv\Scripts\python.exe -c "import PySide6.QtCore, mwparserfromhell, jsonschema, requests" >nul 2>&1
if errorlevel 1 goto prepare
:launch
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0run_gui.py"
exit /b 0
:prepare
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failure
.venv\Scripts\python.exe -m pip install -e . --no-deps
if errorlevel 1 goto failure
goto launch
:failure
echo Failed to prepare Python environment. See README.md. Python 3.12+ is required.
pause
exit /b 1
