@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv-server\Scripts\python.exe" (
  py -3 -m venv .venv-server
  if errorlevel 1 goto failure
)
.venv-server\Scripts\python.exe -m pip install ".[server]"
if errorlevel 1 goto failure
.venv-server\Scripts\python.exe scripts\check_api_local.py
set "check_result=%errorlevel%"
pause
exit /b %check_result%
:failure
echo Could not prepare environment. Python 3.12+ is required.
pause
exit /b 1
