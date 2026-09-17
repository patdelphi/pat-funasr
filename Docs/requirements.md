#
运行环境与依赖约束（Requirements）

## 目标形态

- 该仓库是 "FunASR" 的 Windows 便携包（GPU 优先），包含源码（"app/"）与内置运行时（"runtime/python/"）。
- 通过设置 "PYTHONPATH" 与模型缓存环境变量，将 API 服务与 UI 运行在便携目录内。
- 运行时包版本清单见根目录 ["requirements.txt"](../requirements.txt)（与内置虚拟环境对齐）；CI 最小测试依赖见 ["requirements-ci.txt"](./requirements-ci.txt)。

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
  - "run_api.bat"/"run_ui_pat.bat" 会把 "runtime/python/Lib/site-packages/torch/lib" 加入 "PATH"，用于加载 torch 自带的 CUDA 依赖
- GPU 检测：
  - Python 辅助脚本：["scripts/detect_gpu.py"](../scripts/detect_gpu.py)
  - 启动器：["start_services.py"](../start_services.py)

## 模型与缓存目录

模型统一存入**本机共享缓存**（多项目共享，便携包不复制模型进工程目录）：

- ModelScope 缓存根： "C:\Users\<你>\.cache\modelscope\hub\models"（语音模型，如 SenseVoice / Paraformer / Qwen3-ASR / emotion2vec）
- HuggingFace 缓存根： "C:\Users\<你>\.cache\huggingface\hub"（翻译模型 TranslateGemma，含 GGUF 量化版）
- 项目兜底目录： "workspace/models"（仅当共享缓存未命中时）

### 查找顺序（server._model_cache_roots）

1. 环境变量 "MODELSCOPE_CACHE" / "HUGGINGFACE_HUB_CACHE"（若已设置，优先）
2. ModelScope 全局缓存 "~/.cache/modelscope/hub/models"
3. HuggingFace 全局缓存 "~/.cache/huggingface/hub"（兼容 "models--Org--Name/snapshots/<hash>/" 布局）
4. 项目 "workspace/models" 兜底

### 下载位置

- 语音类模型：默认走 ModelScope 源（hub=ms），由 FunASR 内部 download_from_ms 下载到 ModelScope 全局缓存
- 翻译模型 TranslateGemma（含 GGUF）：仅存在于 HuggingFace，本地未命中时 server.py 自动 snapshot_download 到 HF 全局缓存后加载
- 需联网时先获得确认；已下载的模型在 "check_latest=False" 下不会主动联网

### 加载环境（GPU 优先，自动回退 CPU）

- 语音/情感/说话人模型：FunASR AutoModel，device 取启动参数（默认 cuda）
- 翻译 NLLB 600M/1.3B：fp16 加载到 GPU，加载失败回退 CPU
- 翻译 TranslateGemma-4B-IT GGUF：llama.cpp 推理（n_gpu_layers=-1），显存余量 <4GB 时先卸载 FunASR 模型腾显存重试，仍不足回退 CPU

## 端口与服务地址

- API（FastAPI + Uvicorn）：默认 "http://localhost:8000"
- Pat WebUI（Gradio）：默认 "http://localhost:7861"

## 关键环境变量（启动脚本会设置）

- "PYTHONPATH"：
  - "runtime/python"
  - "runtime/python/Lib/site-packages"
  - "app"
- "PATH"（仅脚本层面追加 torch 的 DLL 目录）：
  - "runtime/python/Lib/site-packages/torch/lib"
- 可选（默认不设置，让模型走本机共享缓存）：
  - "MODELSCOPE_CACHE" / "HUGGINGFACE_HUB_CACHE"：覆写模型缓存根（优先级最高）
  - "FUNASR_MODEL_HUB"：切换默认模型源为 "ms" / "hf"（见 ["switch_model_hub.bat"](../switch_model_hub.bat)）
  - "FUNASR_MODEL_IDLE_TTL_S"：模型闲置释放超时秒数（默认 1800，0=禁用）
