@echo off
REM Program: start FunASR API and Pat WebUI in a single CMD window.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "DBG_LOG=%~dp0trae-debug-log-bat-startup-failure.txt"
set "API_LOG=%~dp0funasr-api.log"
set "UI_LOG=%~dp0funasr-ui.log"
(echo ==== START %date% %time% ==== & echo SCRIPT=%~f0 & echo ROOT=%~dp0 & echo COMSPEC=%ComSpec%)>>"%DBG_LOG%"
(
  echo ==================================================
  echo FunASR API log
  echo started at %date% %time%
  echo root=%~dp0
  echo ==================================================
)>"%API_LOG%"
(
  echo ==================================================
  echo FunASR UI log
  echo started at %date% %time%
  echo root=%~dp0
  echo ==================================================
)>"%UI_LOG%"
set "DEVICE=%~1"
if "%DEVICE%"=="" set "DEVICE=cuda"
echo DEVICE=%DEVICE%>>"%DBG_LOG%"
if not exist "%~dp0run_api.bat" (
  echo ERROR: missing "run_api.bat"
  echo ERROR: missing run_api.bat>>"%DBG_LOG%"
  pause
  exit /b 1
)
if not exist "%~dp0run_ui_pat.bat" (
  echo ERROR: missing "run_ui_pat.bat"
  echo ERROR: missing run_ui_pat.bat>>"%DBG_LOG%"
  pause
  exit /b 1
)
set "PS_EXE=powershell"
where pwsh >nul 2>nul
if "%ERRORLEVEL%"=="0" set "PS_EXE=pwsh"
echo PS_EXE=%PS_EXE%>>"%DBG_LOG%"
echo [1/2] starting API: "run_api.bat" (DEVICE=%DEVICE%)
set "FUNASR_SINGLE_WINDOW=1"
echo CMD_API=%PS_EXE% -NoProfile -Command "Start-Process -FilePath '%ComSpec%' -ArgumentList '/c call ""%~dp0run_api.bat"" ""%DEVICE%"" >> ""%API_LOG%"" 2^>^&1' -WorkingDirectory '%~dp0' -WindowStyle Hidden" >>"%DBG_LOG%"
"%PS_EXE%" -NoProfile -Command "Start-Process -FilePath '%ComSpec%' -ArgumentList '/c call ""%~dp0run_api.bat"" ""%DEVICE%"" >> ""%API_LOG%"" 2^>^&1' -WorkingDirectory '%~dp0' -WindowStyle Hidden"
if errorlevel 1 (
  echo ERROR: failed to start hidden API process
  echo ERROR: failed to start hidden API process>>"%DBG_LOG%"
  pause
  exit /b 1
)
set "UI_PORT="
for /f %%P in ('%PS_EXE% -NoProfile -Command "$ports = 7861,7862,7863; $used = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty LocalPort -Unique; $free = $ports | Where-Object { $_ -notin $used } | Select-Object -First 1; if ($free) { Write-Output $free }"') do set "UI_PORT=%%P"
if "%UI_PORT%"=="" (
  echo ERROR: no free UI port found in 7861,7862,7863
  echo ERROR: no free UI port found>>"%DBG_LOG%"
  pause
  exit /b 1
)
set "FUNASR_UI_PORT=%UI_PORT%"
echo [2/2] starting UI: "run_ui_pat.bat" (port %UI_PORT%)
echo CMD_UI=%PS_EXE% -NoProfile -Command "Start-Process -FilePath '%ComSpec%' -ArgumentList '/c call ""%~dp0run_ui_pat.bat"" >> ""%UI_LOG%"" 2^>^&1' -WorkingDirectory '%~dp0' -WindowStyle Hidden" >>"%DBG_LOG%"
"%PS_EXE%" -NoProfile -Command "Start-Process -FilePath '%ComSpec%' -ArgumentList '/c call ""%~dp0run_ui_pat.bat"" >> ""%UI_LOG%"" 2^>^&1' -WorkingDirectory '%~dp0' -WindowStyle Hidden"
if errorlevel 1 (
  echo ERROR: failed to start hidden UI process
  echo ERROR: failed to start hidden UI process>>"%DBG_LOG%"
  pause
  exit /b 1
)
echo launched: API=8000, Pat WebUI=%UI_PORT%
echo API log: "%API_LOG%"
echo UI log: "%UI_LOG%"
echo open browser after WebUI is ready: http://127.0.0.1:%UI_PORT%
echo.
echo ==================== Live Logs ====================
"%PS_EXE%" -NoProfile -Command "Get-Content -Path '%API_LOG%','%UI_LOG%' -Wait -Tail 200"
endlocal
exit /b 0
