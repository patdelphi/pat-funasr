@echo off
REM Program: start the standalone browser Mic diagnostic page for Pat-FunASR.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PORT=%~1"
if "%PORT%"=="" set "PORT=7870"
if not exist "%~dp0runtime\python\python.exe" (
  echo ERROR: missing "runtime\python\python.exe"
  pause
  exit /b 1
)
echo Starting Mic test page on http://127.0.0.1:%PORT%
"%~dp0runtime\python\python.exe" -X utf8 "%~dp0aipython\mic_test_server.py" --port "%PORT%"
endlocal
