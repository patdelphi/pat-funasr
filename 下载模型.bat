@echo off
set "ROOT=%~dp0"
set "PYTHON=%ROOT%runtime\python\python.exe"

echo ==========================================
echo  Download Model
echo ==========================================
echo.

if not exist "%PYTHON%" (
    echo [ERROR] Python not found
    pause
    exit /b 1
)

echo [INFO] Download SenseVoice model (~900MB)
echo [INFO] Save to: workspace\models
echo.
pause
echo.

set "PYTHONPATH=%ROOT%runtime\python;%ROOT%runtime\python\Lib\site-packages;%ROOT%app"
"%PYTHON%" "%ROOT%scripts\download_model.py"

echo.
pause
