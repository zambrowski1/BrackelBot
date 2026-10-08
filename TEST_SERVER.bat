@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv-server\Scripts\python.exe" (
  py -3 -m venv .venv-server
  if errorlevel 1 goto failure
)
.venv-server\Scripts\python.exe -m pip install ".[server,test]"
if errorlevel 1 goto failure
.venv-server\Scripts\python.exe -m pytest -q
set "test_result=%errorlevel%"
pause
exit /b %test_result%
:failure
echo Could not prepare server tests. Python 3.12+ is required.
pause
exit /b 1
