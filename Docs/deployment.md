#
本地运行与部署建议（Deployment）

## 推荐启动方式（更稳）

使用 "start_services.py"（会自动选择 "cuda/cpu"，并等待 API 就绪）：

- 入口文件：["start_services.py"](file:///y:/NewStore/AI/FunASR-Portable-GPU/start_services.py)
- 启动后地址：
  - API："http://localhost:8000"
  - UI："http://localhost:7860"

## 一键脚本启动

使用 "FunASR.bat"：

- 入口文件：["FunASR.bat"](file:///y:/NewStore/AI/FunASR-Portable-GPU/FunASR.bat)
- 会启动两个独立窗口：
  - "run_api.bat"（API）
  - "run_ui.bat"（UI）

注意（从脚本逻辑可见）：

- "FunASR.bat" 会检测 GPU，并把探测到的 device 传给 "run_api.bat"
- 若需要在无 CUDA 的机器上运行，建议优先使用 "start_services.py"（它会自动选择 cpu/cuda 并等待 API 就绪）

## 停止服务

脚本：["停止服务.bat"](file:///y:/NewStore/AI/FunASR-Portable-GPU/停止服务.bat)

- 通过窗口标题过滤杀进程：WINDOWTITLE = "FunASR-API*" / "FunASR-UI*"
- 不再按内存阈值误杀 "python.exe"

## 模型下载与离线

### 自动下载（默认行为）

- API 启动时会预加载一个模型（默认 "sensevoice"），若本地无缓存会触发从 Hub 下载
- 缓存目录被固定到工程内（见 ["requirements.md"](file:///y:/NewStore/AI/FunASR-Portable-GPU/Docs/requirements.md)）

### 手动下载（离线准备）

- ModelScope/HF 均可将模型提前下载到 "workspace/models" 对应目录
- 参考模型自带 README（例如 SenseVoiceSmall 的 "workspace/models/models/iic/SenseVoiceSmall/README.md"）

### 发现的问题：下载脚本缺失

- "下载模型.bat" 会调用 "scripts/download_model.py" 进行模型下载与缓存预热

## 跑批测试（test 目录）

用途：遍历 "test\\" 下音视频文件，按模型分别输出 ASR 结果到 "test\\<模型名>\\"。

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File ".\run_test_all_models.ps1"
```

## 常见排错点（便携包特有）

- torch CUDA DLL 找不到：需要把 "runtime/python/Lib/site-packages/torch/lib" 加入 "PATH"
  - 已在 "run_api.bat"/"run_ui.bat" 里做了追加
- Python/依赖不匹配：用 ["检查环境.bat"](file:///y:/NewStore/AI/FunASR-Portable-GPU/检查环境.bat) 快速验证
- 端口占用：确认 8000/7860 未被其他程序占用
