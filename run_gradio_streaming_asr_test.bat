@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
"%~dp0runtime\python\python.exe" -X utf8 "%~dp0aipython\gradio_streaming_asr_test.py" --port 7872 %*
