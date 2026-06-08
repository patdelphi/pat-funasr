@echo off
echo Stopping FunASR services...
taskkill /F /FI "WINDOWTITLE eq FunASR-API*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq FunASR-UI*" >nul 2>&1
taskkill /F /IM python.exe /FI "MEMUSAGE gt 50000" >nul 2>&1
echo Done.
pause
