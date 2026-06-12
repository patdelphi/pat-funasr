@echo off
REM Program: start FunASR OpenAI API and reuse the current CMD in single-window mode.
setlocal
chcp 65001 >nul
set "DEVICE=%~1"
if "%DEVICE%"=="" set "DEVICE=cuda"
if not defined FUNASR_SINGLE_WINDOW title FunASR-API (%DEVICE%)
cd /d "%~dp0app\openai_api"
if exist "%~dp0.env.local.bat" call "%~dp0.env.local.bat"
set "MODELSCOPE_CACHE=%~dp0workspace\models"
set "HF_HOME=%~dp0workspace\models\huggingface"
set "PYTHONPATH=%~dp0runtime\python;%~dp0runtime\python\Lib\site-packages;%~dp0app"
REM Add torch CUDA DLL path
set "PATH=%~dp0runtime\python\Lib\site-packages\torch\lib;%PATH%"
"%~dp0runtime\python\python.exe" -X utf8 server.py --model sensevoice --device "%DEVICE%" --port 8000
endlocal

