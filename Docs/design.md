#
架构与关键数据流（Design）

## 总体组件

- 启动层
  - "FunASR.bat"：一键启动（会启动 2 个新窗口分别跑 API/UI）
  - "start_services.py"：单进程拉起 API + UI，并内置健康检查等待逻辑
- 服务层
  - "app/openai_api/server.py"：OpenAI 兼容音频转写 API（FastAPI）
  - "app/openai_api/gradio_app.py"：浏览器 Demo（Gradio，调用 API）
- 模型层
  - "app/funasr"：FunASR 源码（AutoModel、前后处理、模型实现等）
  - "workspace/models"：模型缓存与数据（ModelScope/HF）
- 运行时层
  - "runtime/python"：嵌入式 Python 与 site-packages（包含 torch 等依赖）

## 启动链路

### 路径 A：一键脚本（"FunASR.bat"）

1. 校验 "runtime/python/python.exe"、"app/openai_api/server.py"、"app/openai_api/gradio_app.py" 是否存在
2. 可选检测模型文件：检查 SenseVoiceSmall 的 "model.pt" 是否存在
3. GPU 探测：执行 "scripts/detect_gpu.py"（通过 torch.cuda.is_available()）
4. 启动两个窗口：
   - API：执行 "run_api.bat"
   - UI：执行 "run_ui.bat"

### 路径 B：推荐启动器（"start_services.py"）

1. 设置缓存目录与 "PYTHONPATH"
2. 在同一进程内探测 torch / CUDA，决定 "device=cuda|cpu"
3. 拉起 API 子进程（"server.py --device <device> --port 8000"）
4. 轮询 "http://localhost:8000/health" 等待 API 就绪（最多约 20s）
5. 拉起 UI 子进程（"gradio_app.py --base-url http://localhost:8000 --port 7860"）
6. 自动打开浏览器

## 请求处理链路（API）

端点：POST "/v1/audio/transcriptions"

1. 接收上传文件（FastAPI UploadFile）
2. 将上传内容写入临时文件（NamedTemporaryFile，后缀尽量保留）
3. 模型加载（首次请求或预加载时）：
   - 根据表 "MODEL_CONFIGS" 选择模型参数
   - 通过 FunASR 的 "AutoModel(**cfg)" 构建推理器
   - 缓存在进程内 "MODEL_REGISTRY"（同一 model 名不会重复加载）
4. 推理：
   - 调用 "asr_model.generate(input=<tmp_path>, batch_size=1, language=<optional>)"
   - 清理特殊标记（"clean_text" 会剥离 SenseVoice 的 "<|...|>" 等 token）
5. 响应：
   - "response_format=json"：返回 {"text": "..."}
   - "response_format=verbose_json"：返回 {"text","segments","language","duration","model"}
6. 清理：删除临时文件

## 请求处理链路（UI）

- Gradio 端选择音频/视频输入
- 若输入为视频，UI 侧会调用 ffmpeg 抽取音频为 16k 单声道 wav（见 "extract_audio_from_video"）
- UI 以 multipart/form-data 调用 API "/v1/audio/transcriptions"
- 展示 "text" 与原始 JSON

## 关键设计点

- 缓存固定在 "workspace/models"：便携、可离线、可整体迁移
- 设备选择由启动层决定（"server.py" 通过 "--device" 控制）
- API 兼容 OpenAI：便于接入现有 Agent/客户端（例如 OpenAI SDK、LangChain 等）

## 风险与已知不一致（从代码静态分析得到）

- "FunASR.bat" 会输出 “No CUDA → Falling back to CPU”，但随后仍直接启动 "run_api.bat"（其固定为 "--device cuda"）
  - 结果：无 GPU 机器上，API 可能直接启动失败或报错
  - 建议：无 GPU 时改走 "start_services.py" 或让 "run_api.bat" 支持参数化 device

