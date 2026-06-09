cls
@echo off
REM Program: start FunASR API and Pat WebUI in a managed single CMD window.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "DEVICE=%~1"
if "%DEVICE%"=="" set "DEVICE=cuda"
if not exist "%~dp0runtime\python\python.exe" (
  echo ERROR: missing "runtime\python\python.exe"
  pause
  exit /b 1
)
if not exist "%~dp0aipython\managed_single_window_launcher.py" (
  echo ERROR: missing "aipython\managed_single_window_launcher.py"
  pause
  exit /b 1
)
echo Starting managed single-window launcher...
"%~dp0runtime\python\python.exe" -X utf8 "%~dp0aipython\managed_single_window_launcher.py" --device "%DEVICE%"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal
exit /b %EXIT_CODE%
