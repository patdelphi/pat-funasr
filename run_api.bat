@echo off
title FunASR-API (GPU)
cd /d "%~dp0app\openai_api"
set "MODELSCOPE_CACHE=%~dp0workspace\models"
set "HF_HOME=%~dp0workspace\models\huggingface"
set "TRANSFORMERS_CACHE=%~dp0workspace\models\transformers"
set "PYTHONPATH=%~dp0runtime\python;%~dp0runtime\python\Lib\site-packages;%~dp0app"
REM cu118 torch 自带 CUDA DLL，需要把 torch\lib 加到 PATH 才能被 python 找到
set "PATH=%~dp0runtime\python\Lib\site-packages\torch\lib;%PATH%"
"%~dp0runtime\python\python.exe" -X utf8 server.py --model sensevoice --device cuda --port 8000
