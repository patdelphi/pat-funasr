@echo off
set "ROOT=%~dp0"
set "PYTHON=%ROOT%runtime\python\python.exe"

echo ==========================================
echo  FunASR-GPU Environment Check
echo ==========================================
echo.

if not exist "%PYTHON%" (
    echo [FAIL] Python runtime not found: %PYTHON%
    echo        Please re-extract the package or reinstall the runtime.
    pause
    exit /b 1
)
echo [PASS] Python runtime found.
echo.

set "PYTHONPATH=%ROOT%runtime\python;%ROOT%runtime\python\Lib\site-packages;%ROOT%app"

echo --- FunASR ---
"%PYTHON%" -X utf8 -c "import funasr; print('funasr', funasr.__version__)" 2>nul || echo [FAIL] funasr not importable
echo.
echo --- PyTorch + CUDA ---
"%PYTHON%" -X utf8 -c "import torch; print('torch', torch.__version__, '| cuda runtime', torch.version.cuda); print('cuda available:', torch.cuda.is_available())" 2>nul || echo [FAIL] torch not importable
echo.
echo --- GPU device ---
"%PYTHON%" -X utf8 -c "import torch; \
import sys; \
sys.exit(0 if torch.cuda.is_available() else 1)" 2>nul
if %ERRORLEVEL% EQU 0 (
    echo [PASS] CUDA GPU detected:
    "%PYTHON%" -X utf8 -c "import torch; \
n=torch.cuda.get_device_name(0); \
c=torch.cuda.get_device_capability(0); \
m=torch.cuda.get_device_properties(0).total_memory/(1024**3); \
print(f'  {n}  |  compute {c[0]}.{c[1]}  |  {m:.1f} GB VRAM')"
) else (
    echo [WARN] No CUDA GPU detected.
    echo        This package is tuned for NVIDIA GPUs.
    echo        It will still work, but inference will fall back to CPU and be slow.
)
echo.
echo --- Model files ---
if exist "%ROOT%workspace\models\iic\SenseVoiceSmall\model.pt" (
    echo [PASS] SenseVoice model present.
) else (
    echo [WARN] SenseVoice model missing - run 下载模型.bat
)
echo.
echo ==========================================
echo  Check complete.
echo ==========================================
pause
