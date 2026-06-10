@echo off
REM Program: start the standalone Gradio Mic streaming diagnostic page.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PORT=%~1"
if "%PORT%"=="" set "PORT=7871"
if not exist "%~dp0runtime\python\python.exe" (
  echo ERROR: missing "runtime\python\python.exe"
  pause
  exit /b 1
)
echo Starting Gradio Mic test page on http://127.0.0.1:%PORT%
set "PYTHONPATH=%~dp0runtime\python;%~dp0runtime\python\Lib\site-packages;%~dp0app"
"%~dp0runtime\python\python.exe" -X utf8 "%~dp0aipython\gradio_mic_test.py" --port "%PORT%"
endlocal
