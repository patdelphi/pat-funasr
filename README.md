# FunASR-Portable-GPU

FunASR 工业级语音识别工具包的 GPU 加速便携版。

## 与 CPU 版的核心差异

| 维度 | CPU 版 | GPU 版 |
|---|---|---|
| 默认设备 | `--device cpu` | `--device cuda` |
| PyTorch | 通用版（2.5.0） | **CUDA 版（2.5.0+cu121）** |
| 启动检测 | 无 | 自动检测 CUDA，无 GPU 时降级为 CPU |
| 启动脚本 | `FunASR.bat` | `启动 FunASR.bat`（中英兼容名） |
| 性能参考 | 17x 实时（SenseVoice） | **80–170x 实时**（按显卡） |
| 硬件要求 | 任意 | NVIDIA 显卡（计算能力 ≥ 3.5） |

## 文件结构（相对 CPU 版新增/修改的部分）

```
FunASR-Portable-GPU/
├── README.md                    ← 本文件（区别说明）
├── README.txt                   ← 用户使用手册（含中文乱码说明）
├── CUDA_GUIDE.md                ← CUDA 部署/性能/故障排查指南（新增）
├── 启动 FunASR.bat              ← 一键启动（GPU 自动检测，区别于 CPU 版的 FunASR.bat）
├── run_api.bat                  ← 修改：--device cuda
├── run_ui.bat                   ← 同 CPU 版
├── start_services.py            ← 重写：CUDA 检测 + 设备降级
├── p0_test.py                   ← 调整：启动等待 20s（CUDA 初始化稍慢）
├── 检查环境.bat                 ← 重写：增加 CUDA / GPU 检测
├── 停止服务.bat                 ← 同 CPU 版
├── 下载模型.bat                 ← 同 CPU 版
├── app/                         ← 同 CPU 版（FunASR 源码）
├── runtime/                     ← 需替换为 CUDA 版 Python + torch+cu121
└── workspace/models/            ← 同 CPU 版（4 款模型可直接复用）
```

## 构建步骤

GPU 版不能直接用 CPU 版的 `runtime/`，需要替换两样东西：

### 1. Python 运行时（嵌入式 3.11.9）
直接从 [python.org](https://www.python.org/downloads/release/python-3119/) 下载 `python-3.11.9-embed-amd64.zip`，解压到 `runtime/python/`。

### 2. PyTorch + CUDA 依赖
启动 `runtime\python\python.exe -m pip install`，按以下顺序安装：

```bash
# CUDA 12.1 版 PyTorch
runtime\python\python.exe -m pip install ^
  torch==2.5.0+cu121 ^
  torchaudio==2.5.0+cu121 ^
  --index-url https://download.pytorch.org/whl/cu121

# FunASR + 服务依赖
runtime\python\python.exe -m pip install ^
  funasr modelscope huggingface_hub ^
  fastapi uvicorn python-multipart ^
  gradio openai-whisper
```

> 完整依赖列表参见 `app/openai_api/Dockerfile`（CPU 版同款依赖，加上 GPU 版特有的 torch+cu121）。

### 3. 模型
直接从 CPU 版 `workspace/models/` 整个拷过来，或重新跑 `下载模型.bat`。

## 快速验证

```bash
启动 FunASR.bat
```

- 有 NVIDIA 显卡 + 驱动 + CUDA → 自动用 `--device cuda`
- 无 CUDA → 自动降级为 `--device cpu`，并打印警告

跑冒烟测试：

```bash
runtime\python\python.exe p0_test.py
```

## 关键改动点（代码层面）

### `启动 FunASR.bat`
- 调用 `python -c "import torch; torch.cuda.is_available()"` 探测 GPU
- 探测成功时打印 `Detected: <GPU 型号>`
- 探测失败时打印 `No CUDA GPU detected. Falling back to CPU.`

### `start_services.py`
- 同样探测 CUDA，失败时把 `device` 改成 `"cpu"`
- 打印显卡型号 + 显存 + `torch` 的 CUDA 版本 + 设备能力

### `run_api.bat`
- 唯一区别：`--device cuda` 替代 `cpu`

### `p0_test.py`
- 启动等待从 18s 提到 20s（CUDA context 初始化 + 首次模型加载到 GPU 都比 CPU 慢）

## 适用场景

- 短视频/直播字幕 → 单条 30s 音频用 GPU 比 CPU 快 5–10 倍
- 长会议录音批量转写 → RTX 3060 上一小时会议 30 秒出稿
- 多人并发 Agent 接入 → OpenAI 兼容 API 接多个客户端，GPU 扛得住
- 短音频场景（< 1s） → CPU 启动开销小，GPU 反而没优势，**短音频建议 CPU**

## 资源

- 详细 CUDA 部署/性能/调优：[CUDA_GUIDE.md](CUDA_GUIDE.md)
- FunASR 官方仓库：https://github.com/modelscope/FunASR
- PyTorch CUDA 安装：https://pytorch.org/get-started/locally/
- NVIDIA 驱动：https://www.nvidia.com/Download/index.aspx
