# FunASR GPU 版 - CUDA 部署指南

## 概述

本便携包是 FunASR 的 GPU 加速版本，默认使用 CUDA 进行推理。相比 CPU 版，推理速度可提升 **5-10 倍**。

---

## 系统要求

### 硬件
- **NVIDIA 显卡**（支持 CUDA）
- 显存：4GB+（单模型），6GB+（推荐）
- 内存：8GB+
- 计算能力：≥ 3.5（GTX 700 系列及以上）

### 软件
- Windows 10/11 64 位
- NVIDIA 驱动：≥ 525.x（推荐 545+）
- CUDA Toolkit：11.8 / 12.1（运行时已包含，无需单独安装）
- cuDNN：已集成在 PyTorch 中

---

## 快速开始

### 1. 启动服务

双击 **`启动 FunASR.bat`**

脚本会自动：
1. 检测 CUDA 可用性
2. 加载模型到 GPU
3. 启动 API 服务（端口 8000）
4. 启动 Gradio 界面（端口 7860）

启动时间：约 10-20 秒（首次加载模型较慢）

### 2. 验证 GPU 加速

打开 `http://localhost:8000/health`，返回示例：

```json
{
  "status": "ok",
  "device": "cuda",
  "gpu": "NVIDIA GeForce RTX 3060",
  "models_loaded": ["sensevoice"]
}
```

### 3. 测试推理速度

上传一段 1 分钟的音频：
- GPU 推理：约 0.5-2 秒
- CPU 推理：约 10-30 秒

---

## 显卡兼容性

### 推荐显卡

| 显卡 | 显存 | SenseVoice | Paraformer | Fun-ASR-Nano |
|------|------|-----------|-----------|--------------|
| RTX 4090 | 24GB | 170x 实时 | 120x 实时 | 17x 实时 |
| RTX 4080 | 16GB | 150x 实时 | 100x 实时 | 15x 实时 |
| RTX 4070 | 12GB | 130x 实时 | 90x 实时 | 14x 实时 |
| RTX 3060 | 12GB | 80x 实时 | 60x 实时 | 12x 实时 |
| RTX 3050 | 8GB | 60x 实时 | 45x 实时 | 10x 实时 |
| GTX 1660 | 6GB | 40x 实时 | 30x 实时 | 8x 实时 |

### 不支持的显卡

- GTX 600 系列及更早（计算能力 < 3.5）
- 笔记本集显
- AMD 显卡（需使用 ROCm 编译的 PyTorch，本便携包不支持）

---

## 性能优化

### 1. 半精度推理（FP16）

修改 `start_services.py`，添加 `torch.float16`：

```python
model = AutoModel(model="iic/SenseVoiceSmall", device="cuda", dtype="float16")
```

可节省 ~40% 显存，速度提升 20-30%。

### 2. 批处理

修改 `app/openai_api/server.py`，启用批处理：

```python
# 在 model 加载后添加
model = AutoModel(model="...", device="cuda", batch_size=4)
```

适合并发请求场景。

### 3. 模型预热

首次推理较慢（CUDA 初始化），可在启动后预热：

```bash
curl http://localhost:8000/v1/audio/transcriptions \
  -F file=@test.wav \
  -F model=sensevoice
```

---

## 故障排查

### 问题 1：CUDA 不可用

**症状**：
```
torch.cuda.is_available() = False
```

**解决方案**：

1. 检查 NVIDIA 驱动：
   ```bash
   nvidia-smi
   ```
   应该看到显卡信息和驱动版本。

2. 检查 CUDA 版本兼容性：
   - 驱动 ≥ 525 支持 CUDA 12.1
   - 驱动 ≥ 450 支持 CUDA 11.8

3. 重新安装 PyTorch（如果需要）：
   ```bash
   pip install torch==2.5.0+cu121 --index-url https://download.pytorch.org/whl/cu121
   ```

### 问题 2：显存不足

**症状**：
```
RuntimeError: CUDA out of memory
```

**解决方案**：

1. 关闭其他占用显存的程序（游戏、深度学习训练等）

2. 减少批处理大小：
   ```python
   model = AutoModel(model="...", device="cuda", batch_size=1)
   ```

3. 使用 FP16：
   ```python
   model = AutoModel(model="...", device="cuda", dtype="float16")
   ```

4. 改用 CPU 版便携包

### 问题 3：模型加载慢

**症状**：启动后需要 30+ 秒才能响应

**原因**：
- 首次加载需要初始化 CUDA 上下文
- 模型从磁盘读取到 GPU 显存

**优化**：
- 将模型放在 SSD 上（NVMe 推荐）
- 预热 GPU：启动后立即发送一个测试请求

### 问题 4：推理速度慢

**症状**：GPU 推理比 CPU 还慢

**可能原因**：
1. 音频文件太小（GPU 启动开销 > 推理时间）
2. 批量处理未启用
3. 其他程序占用 GPU 资源

**检查方法**：
```bash
nvidia-smi
# 查看 GPU 利用率
```

如果 GPU 利用率 < 50%，考虑批处理或使用更大的输入。

---

## 高级配置

### 多 GPU 支持

如果有多个 GPU，可以指定使用哪一块：

```python
model = AutoModel(model="iic/SenseVoiceSmall", device="cuda:0")  # 第一块
model = AutoModel(model="iic/SenseVoiceSmall", device="cuda:1")  # 第二块
```

### TensorRT 加速（实验性）

如需进一步提升性能，可使用 TensorRT：

```bash
pip install tensorrt
```

然后在 `server.py` 中添加：

```python
import torch
from torch2trt import torch2trt

# 转换模型
model_trt = torch2trt(model, [input_tensor])
```

> 注意：TensorRT 需要额外配置，不建议新手使用。

### 显存监控

启动后查看显存使用：

```bash
nvidia-smi -l 1
```

或使用 Python：

```python
import torch
print(f"已分配: {torch.cuda.memory_allocated()/1024**3:.2f} GB")
print(f"已缓存: {torch.cuda.memory_reserved()/1024**3:.2f} GB")
```

---

## 卸载

GPU 版便携包是完全绿色的，删除文件夹即可彻底卸载。

不会：
- 修改系统环境变量
- 安装系统服务
- 修改注册表

---

## 与 CPU 版对比

| 特性 | GPU 版 | CPU 版 |
|------|--------|--------|
| 推理速度 | 快（5-10x） | 慢 |
| 硬件要求 | NVIDIA 显卡 | 任意 |
| 显存占用 | 2-4GB | 无 |
| 便携性 | 需 CUDA 驱动 | 纯绿色 |
| 适用场景 | 生产环境、高并发 | 个人学习、低频使用 |

**建议**：
- 有 N 卡 → 使用 GPU 版
- 无 N 卡或显卡老旧 → 使用 CPU 版
- 两种包可共存于不同目录

---

## 参考资料

- [FunASR 官方仓库](https://github.com/modelscope/FunASR)
- [PyTorch CUDA 安装指南](https://pytorch.org/get-started/locally/)
- [CUDA 兼容性列表](https://developer.nvidia.com/cuda-gpus)
- [NVIDIA 驱动下载](https://www.nvidia.com/Download/index.aspx)

---

**有问题？** 运行 `检查环境.bat` 查看详细诊断信息。
