@echo off
title Pat-FunASR-UI (GPU)
cd /d "%~dp0app\pat_funasr_webui"
set "PYTHONPATH=%~dp0runtime\python;%~dp0runtime\python\Lib\site-packages;%~dp0app"
set "PATH=%~dp0runtime\python\Lib\site-packages\torch\lib;%PATH%"
"%~dp0runtime\python\python.exe" -X utf8 gradio_app.py --base-url http://localhost:8000 --port 7861

