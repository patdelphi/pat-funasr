@echo off
REM Program: start Pat WebUI and reuse the current CMD in single-window mode.
chcp 65001 >nul
setlocal
if not defined FUNASR_SINGLE_WINDOW title Pat-FunASR-UI (GPU)
set "PORT=%FUNASR_UI_PORT%"
if "%PORT%"=="" (
  for /f %%P in ('powershell -NoProfile -Command "$ports = 7861,7862,7863; $used = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty LocalPort -Unique; $free = $ports | Where-Object { $_ -notin $used } | Select-Object -First 1; if ($free) { Write-Output $free }"') do set "PORT=%%P"
)

if "%PORT%"=="" (
  echo ERROR: no free port found in 7861,7862,7863
  pause
  exit /b 1
)

echo Starting Pat WebUI on http://127.0.0.1:%PORT%
cd /d "%~dp0app\pat_funasr_webui"
if exist "%~dp0.env.local.bat" call "%~dp0.env.local.bat"
set "PYTHONPATH=%~dp0runtime\python;%~dp0runtime\python\Lib\site-packages;%~dp0app"
set "PATH=%~dp0runtime\python\Lib\site-packages\torch\lib;%PATH%"
"%~dp0runtime\python\python.exe" -X utf8 gradio_app.py --base-url http://localhost:8000 --port %PORT%
endlocal
