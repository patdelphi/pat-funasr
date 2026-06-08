#
运行环境与依赖约束（Requirements）

## 目标形态

- 该仓库是 "FunASR" 的 Windows 便携包（GPU 优先），包含源码（"app/"）与内置运行时（"runtime/python/"）。
- 通过设置 "PYTHONPATH" 与模型缓存环境变量，将 API 服务与 UI 运行在便携目录内。

## 操作系统

- Windows（脚本为 ".bat"，默认路径分隔符与环境变量均按 Windows 约定）

## Python 运行时

- 入口 Python： "runtime/python/python.exe"
- 运行 UTF-8：脚本中普遍使用 "-X utf8"
- 依赖来源：
  - 便携包内已包含大量依赖于 "runtime/python/Lib/site-packages/"
  - 额外依赖/版本请以 "app/openai_api/Dockerfile" 为参考（见根目录 "README.md" 的说明）

## GPU / CUDA

- 预期设备：NVIDIA GPU（"torch.cuda.is_available()" 为真时启用）
- CUDA DLL 加载：
  - "run_api.bat"/"run_ui.bat" 会把 "runtime/python/Lib/site-packages/torch/lib" 加入 "PATH"，用于加载 torch 自带的 CUDA 依赖
- GPU 检测：
  - Python 辅助脚本：["scripts/detect_gpu.py"](file:///y:/NewStore/AI/FunASR-Portable-GPU/scripts/detect_gpu.py)
  - 启动器：["start_services.py"](file:///y:/NewStore/AI/FunASR-Portable-GPU/start_services.py)

## 模型与缓存目录

便携包把模型缓存固定到工程内，避免写入用户目录：

- ModelScope 缓存根： "MODELSCOPE_CACHE" = "workspace/models"
- HuggingFace 缓存根： "HF_HOME" = "workspace/models/huggingface"
- Transformers 缓存根： "TRANSFORMERS_CACHE" = "workspace/models/transformers"

本地模型示例：

- SenseVoiceSmall： "workspace/models/models/iic/SenseVoiceSmall/model.pt"（脚本会尝试检测）

## 端口与服务地址

- API（FastAPI + Uvicorn）：默认 "http://localhost:8000"
- UI（Gradio）：默认 "http://localhost:7860"

## 关键环境变量（启动脚本会设置）

- "PYTHONPATH"：
  - "runtime/python"
  - "runtime/python/Lib/site-packages"
  - "app"
- "PATH"（仅脚本层面追加 torch 的 DLL 目录）：
  - "runtime/python/Lib/site-packages/torch/lib"

