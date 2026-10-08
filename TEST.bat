@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto failure
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failure
.venv\Scripts\python.exe -m pip install -e . --no-deps
if errorlevel 1 goto failure
.venv\Scripts\python.exe -m pytest -q
set "test_result=%errorlevel%"
echo Tests finished. Exit code: %test_result%
pause
exit /b %test_result%
:failure
echo Failed to prepare tests. See README.md. Python 3.12+ is required.
pause
exit /b 1
