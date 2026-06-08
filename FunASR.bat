@echo off
setlocal

echo ==========================================
echo  FunASR Speech Recognition (GPU)
echo ==========================================
echo.

set "PYTHON=%~dp0runtime\python\python.exe"
set "API_DIR=%~dp0app\openai_api"
set "DETECT=%~dp0scripts\detect_gpu.py"

if not exist "%PYTHON%" (
    echo [ERROR] Python not found: %PYTHON%
    echo Please run 检查环境.bat to verify the runtime.
    pause
    exit /b 1
)

if not exist "%API_DIR%\server.py" (
    echo [ERROR] server.py not found: %API_DIR%\server.py
    pause
    exit /b 1
)
if not exist "%API_DIR%\gradio_app.py" (
    echo [ERROR] gradio_app.py not found: %API_DIR%\gradio_app.py
    pause
    exit /b 1
)

set "MODELS=%~dp0workspace\models"
set "FOUND="
for %%P in ("%MODELS%\models\iic\SenseVoiceSmall\model.pt") do (
    if exist "%%~P" set "FOUND=%%~P"
)
if not defined FOUND (
    echo [WARN] SenseVoiceSmall model not found in workspace\models.
    echo        start_services.py will auto-download from ModelScope on first run.
    echo        This may take 5-10 minutes.
    echo.
)

echo [PASS] Python and source OK
echo.

REM --- GPU detection ----------------------------------------------------
set "DEVICE=cpu"
set "PATH=%~dp0runtime\python\Lib\site-packages\torch\lib;%PATH%"
"%PYTHON%" "%DETECT%" > "%TEMP%\funasr_gpu.txt" 2>nul
if errorlevel 1 goto no_gpu
set /p GPU_NAME=<"%TEMP%\funasr_gpu.txt"
echo [GPU]  Detected: %GPU_NAME%
set "DEVICE=cuda"
goto gpu_done

:no_gpu
echo [WARN] No CUDA GPU detected. Falling back to CPU.
echo        This build is optimized for GPU and may be slow on CPU.
echo        Use the CPU portable package for non-NVIDIA machines.

:gpu_done

echo.
echo Starting services...
echo.

echo Launching API server on http://localhost:8000 ...
start "FunASR-API" cmd /c ""%~dp0run_api.bat" %DEVICE%""

echo.
echo Launching Gradio UI on http://localhost:7860 ...
start "FunASR-UI" cmd /c ""%~dp0run_ui.bat""

ping -n 4 127.0.0.1 >nul
start http://localhost:7860

echo.
echo ==========================================
echo  FunASR GPU Started!
echo ==========================================
echo.
echo API:    http://localhost:8000
echo UI:     http://localhost:7860
echo.
echo Close this window to exit launcher.
echo.
echo Close API/UI windows in taskbar to stop services.
echo.

endlocal
pause
