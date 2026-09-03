# Pat-FunASR

基于 [FunASR](https://github.com/modelscope/FunASR) ，进行了大幅度实用化改造，覆盖 ASR 语音识别、说话人分离、LLM 校对/纪要/脑图、跨语种翻译、情感识别的**完整流水线**。支持本地离线模型 + 外部 LLM（如阿里云通义千问）的混合模式。

核心定位：**隐私优先**（ASR/翻译/情感识别模型全部本地运行）+ **专业级转录产物**（SRT/VTT/字幕时间轴 + 说话人 + 会议纪要 + 思维导图 + 多语言翻译）。

## ✨ 功能特性（Feature Matrix）

### 🔊 语音识别（ASR）
- **长音频分块识别**：>5 分钟音频自动 ffmpeg 切片（默认 4 分钟/块，10 秒重叠），每块独立 ASR 后通过"文本指纹 + 2×重叠时间窗口"去重合并，2 小时录音识别完整度从 4000 字提升到 50000+ 字（+1200%）
- **9 个本地模型**：SenseVoice、Paraformer（中文）、Paraformer-EN、Paraformer-ZH-Streaming、Fun-ASR-Nano、Qwen3-ASR 1.7B / 0.6B
- **双模型对照**：主模型 + 一个或多个校对模型并行/串行运行，`primary_first` 或 `weighted_consensus` 策略自动对齐，避免单一模型系统性错误
- **多模式入口**：离线识别（单文件/批量）、文件流式识别、麦克风实时识别、精细转录工作流
- **强制对齐**：Qwen3-ASR 支持字词级时间戳，句级边界基于原生标点聚合

### 🎙️ 说话人分离（Diarization）
- cam++ 嵌入提取（GPU） + SpectralCluster / UMAP+HDBSCAN 聚类（CPU）
- 支持预设说话人数、全局聚类、VAD 段 / 标点段两种分割模式
- 每段时间轴自动对齐到 ASR 结果，不确定段保留候选

### 🧠 LLM 后处理（外部 LLM，本地文本不上传音频）
- **文本校对（proofread）**：错别字 / 同音词 / 标点 / 断句纠错
  - `scope=refined` 全文拼接 + 内部 6000 字/块流式处理，比逐段调用快 **5-6 ×**（2659 段从 45 分钟降到 7 分钟）
  - 校对后文本**按原 segments 长度比例回填**，保证 SRT/TXT 导出与全文一致
- **会议纪要（summary）**：结构化 JSON，包含 summary / decisions / action_items / notes / topics 等字段，自动转可读 Markdown 导出
- **思维导图（mindmap）**：`title / children` 嵌套 JSON，Gradio 端使用 iframe srcdoc + markmap 渲染，空结果显示黄色警告卡片；多块文本 children 自动合并到同一根节点

### 🌐 翻译（NLLB 本地模型）
- NLLB-200-Distilled 600M / 1.3B，支持 200+ 语言互译
- **长文本自动分块翻译**：按句号/感叹号/问号切分，≤500 字/块逐块翻译，避免 NLLB `max_length=512` 导致的长文本截断与卡死
- 翻译源/目标语言代码严格匹配 NLLB 原生 BCP-47（如 `zho_Hans`、`eng_Latn`）

### ❤️ 情感识别
- emotion2vec-plus-large 本地模型，句段级/帧级输出
- 独立标签或叠加到说话人段

### 💾 产物与格式
- **单格式**：JSON / TXT / SRT / VTT / TSV
- **ZIP 打包（精细转录）**：output.txt、transcript_segments.txt、transcript_refined.txt、output.tsv、output.srt、output.vtt、output.json、summary.md、mindmap.json —— 共 9 个文件
- UTF-8 BOM，Windows 换行符，无乱码

### 🔌 接口与 UI
- **OpenAI 兼容 API**：`/v1/audio/transcriptions`（可替代 Whisper）、`/v1/models`、`/v1/funasr/workflows`（工作流全能力）
- **Gradio WebUI**：
  - 4 个顶层栏目：转录工作台、实时识别、媒体与文本工具、模型与服务
  - 6 个业务子栏目：快速转录、会议精细转录、说话人时间轴、音频处理、跨语言翻译、情感识别
  - 2 个实时识别子页：文件流式识别、Mic 实时识别
  - 业务导航 Tab 不绑定后端 `select` 回调，切换零等待
- **Windows 便携包**：内置 Python 运行时 + CUDA PyTorch，开箱即用

### 🛡️ 稳定性
- LLM 超时拆 connect(10s) / read(300s) 两段，避免单 300s 假死
- 连续 2 次 LLM 失败激活 5 分钟熔断，后续阶段直接短路并在 UI 警告区显示
- SQLite 全部启用 `journal_mode=WAL` + `synchronous=NORMAL`
- 所有 API 有异常处理、数据库操作有事务、所有阶段失败有回退策略（如 reviewer 失败 `skip_failed_reviewer`）

### 🔁 代码复用策略（关键）
长音频 ASR 分块和 NLLB 翻译分块**不是**精细转录工作流独有，而是**自动下沉到公共层**，所有入口受益：

| 能力 | 下沉位置 | 覆盖路径 |
|------|----------|----------|
| **ASR 自动分块**（>5min） | `server.py` `transcribe()`（离线识别 API） | 离线识别 API + 精细转录工作流（后者显式配置 chunk_enabled） |
| **NLLB 翻译分块**（≤500字/块） | `NLLBTranslationModel.translate()` | 工作流翻译 + 独立翻译 Tab + API `/v1/funasr/translate` |
| **LLM 熔断 / connect+read 超时 / enable_thinking=False** | `summary_processor.call_llm()` | 校对 + 纪要 + 脑图 三条 LLM 调用路径 |
| **校对回填 `_redistribute_refined_to_segments`** | `workflow_runner`（纯函数） | 任何 scope=refined/all 的 LLM 后处理场景，import 即可用 |

**设计原则**：能下沉到底层类/方法的不分层调用，能自动触发的不加额外参数。这样新增入口（如批量 API、新 UI Tab）时自动继承稳定性，不会遗漏。

## 🚀 快速开始

### 环境要求
- Windows 10/11
- NVIDIA GPU（默认为 GPU 模式，支持 CPU 模式）
- CUDA 11.8+（PyTorch 已内置）
- 可选：外部 LLM API Key（用于校对/纪要/脑图，写入 `.env`）

### 启动方式
```batch
:: 方式一：单窗口托管启动 API + WebUI（推荐）
FunASR_pat.bat
:: 调用 CPU 模式命令：FunASR_pat.bat cpu

:: 方式二：分别启动
run_api.bat        :: 启动 API 服务（默认端口 8000）
run_ui_pat.bat     :: 启动 WebUI（默认端口 7861）
```

启动后访问：
- WebUI：http://127.0.0.1:7861
- API：http://127.0.0.1:8000
- API 文档：http://127.0.0.1:8000/docs

### 配置外部 LLM（可选，用于校对/纪要/脑图）
复制 `.env.sample` → `.env`，按模板填入 LLM 配置：
```ini
LLM_2_ENABLED=true
LLM_2_NAME=OpenAI
LLM_2_PROVIDER=custom
LLM_2_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_2_API_KEY=sk-your-key-here
LLM_2_MODELS=qwen3.7-plus
```

WebUI 会自动显示已启用的 Provider/模型选项。`.env` 已加入 `.gitignore`。

模型全部下载到 **`C:\Users\<你>\.cache\modelscope\hub\models`** 作为全局缓存，多项目共享。

## 🧪 快速测试
```bash
# 全量单元测试（~300+ 用例，纯本地，不调用外部 API）
python -m pytest tests/ -x -q

# 各子功能常用入口
python -m pytest tests/test_fine_transcription_pipeline.py -x -q   # 精细转录管线
python -m pytest tests/test_workflow_runner.py -x -q              # 工作流调度
python -m pytest tests/test_workflow_ui.py -x -q                 # 前端配置映射
python -m pytest tests/test_summary_processor_circuit_breaker.py -x -q  # LLM 熔断
python -m pytest tests/test_renderers.py -x -q                   # 产物导出
```

真实端到端验证（2h43m 录音实测）：
- 70 分钟音频 IBEC竞标会议录音.m4a，完整精细转录工作流（双模型+校对+纪要+脑图+翻译+说话人）**29.6 分钟全部完成，无一阶段失败**
- 802 segments × 28531 字，校对、纪要 8 段、脑图 26 节点、翻译 40948 字全文英文均正常输出

## 🧠 模型能力矩阵

| 模型 | 离线识别 | 流式识别 | 说话人分离 | 情感识别 | 文本翻译 | 语言覆盖 |
|------|:--------:|:--------:|:----------:|:--------:|:--------:|----------|
| sensevoice | ✅ | - | ✅ | ✅ | - | 普通话/粤语/英语/日语/韩语 + 50+ 语种 |
| paraformer | ✅ | - | ✅ | - | - | 中文/英文 |
| paraformer-en | ✅ | - | - | - | - | 英文 |
| paraformer-zh-streaming | - | ✅ | - | - | - | 中文/英文 |
| fun-asr-nano | ✅ | - | ✅ | - | - | 中文/英文/日文 + 7 种方言 |
| qwen3-asr | ✅ | - | - | - | - | 30 种语言 + 22 种中文方言 |
| emotion2vec-plus-large | - | - | - | ✅ | - | 跨语种 |
| nllb-200-distilled-600m | - | - | - | - | ✅ | 200+ 语言互译 |
| nllb-200-distilled-1.3b | - | - | - | - | ✅ | 200+ 语言互译 |

详见 [模型能力矩阵](Docs/model-capability-matrix.md)。

## 🔗 API 常用示例

### 离线识别
```bash
curl http://localhost:8000/v1/audio/transcriptions \
  -F file=@audio.wav \
  -F model=sensevoice \
  -F response_format=srt
```

### 工作流（精细转录全流程）
```bash
curl http://localhost:8000/v1/funasr/workflows \
  -F file=@meeting.m4a \
  -F workflow='{"workflow_version":"1.0","preset_id":"custom","segmentation":{"chunk_enabled":true},"diarization":{"enabled":true},"llm_proofread":{"enabled":true,"scope":"refined"},"summary":{"enabled":true},"mindmap":{"enabled":true},"translation":{"enabled":true,"source_lang":"zho_Hans","target_lang":"eng_Latn"}}'
```
返回 `job_id` → 轮询 `GET /v1/funasr/workflows/{job_id}` → 完成后下载 ZIP。

### 文本翻译（NLLB）
```bash
curl -X POST http://localhost:8000/v1/funasr/translate \
  -H "Content-Type: application/json" \
  -d '{"text":"你好世界","source_lang":"zho_Hans","target_lang":"eng_Latn","model":"nllb-200-distilled-600m"}'
```

## 📁 项目结构
```
pat-funasr/
├── app/
│   ├── openai_api/                   # OpenAI 兼容 API + 工作流引擎
│   │   ├── server.py                 #   HTTP 服务器
│   │   ├── workflow_runner.py        #   工作流调度（主→校对→对齐→LLM→翻译→导出）
│   │   ├── workflow_service.py       #   WorkflowConfig schema 与校验
│   │   ├── reconciliation_service.py #   多模型候选对齐
│   │   ├── alignment_service.py      #   说话人段/时间轴对齐
│   │   ├── artifact_service.py       #   产物写出
│   │   └── renderers.py              #   SRT/VTT/TSV/JSON/ZIP/summary.md 渲染
│   ├── pat_funasr_webui/             # Gradio WebUI
│   │   ├── gradio_app.py             #   UI 主入口
│   │   ├── workflow_ui.py            #   工作流配置映射
│   │   └── fine_transcription/       #   精细转录前端 & LLM 管线
│   │       ├── summary_processor.py  #     校对/纪要/脑图 + 熔断 + 流式
│   │       ├── transcription_pipeline.py  #  长音频分块 ASR + 合并
│   │       ├── audio_sync_js.py      #     音字联动 + markmap 渲染
│   │       ├── llm_config.py         #     .env LLM 配置读取
│   │       ├── scene_templates.py    #     场景 prompt
│   │       └── store.py              #     词表 SQLite
│   └── funasr/                       # FunASR 核心库（不动）
├── tests/                            # 单元测试（~300 用例）
├── Docs/                             # 项目文档
├── workspace/                        # 运行时产物、临时文件、本地测试结果
├── aipython/                         # Python 工具脚本
├── scripts/                          # 启动/下载/探测脚本
├── .env.sample                       # LLM 配置模板
├── start_services.py                 # 托管启动
├── FunASR_pat.bat                    # 一键启动（推荐）
├── run_api.bat / run_ui_pat.bat      # 分别启动
└── README.md / README_zh.md
```

## 📚 文档索引
- [模型能力矩阵](Docs/model-capability-matrix.md)：模型语言覆盖、能力差异与 API 参数说明
- [API 文档](Docs/api.md)：后端 API 端点、参数、响应格式
- [部署指南](Docs/deployment.md)：本地运行与发布建议
- [运行环境](Docs/requirements.md)：OS/GPU/Python/端口/环境变量约束
- [设计文档](Docs/design.md)：整体架构与关键数据流
- [变更记录](Docs/changelog.md)：版本变更历史
- [工作流 API 文档](app/openai_api/WORKFLOWS_zh.md)：workflow 配置 schema 与阶段说明
- [任务策划与进展](Docs/backend-consolidation-and-fine-transcription-plan-20260823.md)

## 🧩 外部参考
- [FunASR 官方教程](https://modelscope.github.io/FunASR/zh/tutorial.html)
- [FunASR API 文档](https://modelscope.github.io/FunASR/api.html)
- [FunASR GitHub](https://github.com/modelscope/FunASR)

## 📄 许可证
本项目基于 FunASR 封装，请遵守 FunASR 的许可协议。
