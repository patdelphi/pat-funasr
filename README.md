# Pat-FunASR

基于 [FunASR](https://github.com/modelscope/FunASR) ，进行了大幅度实用化改造，覆盖ASR语音识别的各种场景，还加入了一个单独的跨语种翻译功能。

无需API Key，全部为本地化模型应用，确保安全性和隐私性。按需实时下载，同时支持Huggingface与魔塔模型下载（可通过 switch_model_hub.bat 切换模型源）。

## 功能特性

- **多模型支持**：SenseVoice、Paraformer、Fun-ASR-Nano、Qwen3-ASR、Emotion2Vec、NLLB 等
- **多种识别模式**：离线识别、流式识别、说话人分离、情感识别、文本翻译
- **多格式输出**：JSON / TXT / SRT / VTT / TSV / ZIP
- **OpenAI 兼容 API**：可直接替换 OpenAI Whisper API 使用
- **Gradio WebUI**：可视化操作界面，支持单文件、批量处理、实时麦克风识别
- **Windows 便携包**：内置 Python 运行时，开箱即用

## 快速开始

### 环境要求

- Windows 10/11
- NVIDIA GPU（默认为GPU模式，支持 CPU 模式）
- CUDA 11.8+（PyTorch 已内置）

### 启动方式

```bash
# 方式一：单窗口托管启动 API + WebUI（推荐）
FunASR_pat.bat
（调用CPU模式命令为：FunASR_pat.bat cpu）

# 方式二：分别启动
run_api.bat        # 启动 API 服务（默认端口 8000）
run_ui_pat.bat     # 启动 WebUI（默认端口 7861）
```

启动后访问：
- WebUI：http://127.0.0.1:7861
- API：http://127.0.0.1:8000
- API 文档：http://127.0.0.1:8000/docs

## 项目结构

```
pat-funasr/
├── app/
│   ├── openai_api/
│   │   └── server.py              # OpenAI 兼容 API 服务
│   ├── pat_funasr_webui/
│   │   ├── gradio_app.py          # Gradio WebUI 主程序
│   │   ├── app_utils.py           # UI 工具函数
│   │   └── translation_utils.py   # 翻译工具
│   └── funasr/                    # FunASR 核心库
├── aipython/                      # Python 工具脚本
├── scripts/                       # 启动和辅助脚本
├── tests/                         # 单元测试
├── Docs/                          # 项目文档
├── workspace/
│   └── models/                    # 模型缓存目录
├── FunASR_pat.bat                 # 一键启动脚本
├── run_api.bat                    # API 启动脚本
└── run_ui_pat.bat                 # WebUI 启动脚本
```

## API 使用

### 离线识别

```bash
curl http://localhost:8000/v1/audio/transcriptions \
  -F file=@audio.wav \
  -F model=sensevoice \
  -F response_format=srt
```

### 流式识别

```bash
curl -X POST http://localhost:8000/v1/funasr/streaming \
  -H "Content-Type: application/json" \
  -d '{"model": "paraformer-zh-streaming", "chunk_size": "0,10,5"}'
```

### 文本翻译

```bash
curl -X POST http://localhost:8000/v1/funasr/translate \
  -H "Content-Type: application/json" \
  -d '{"text": "你好世界", "source_lang": "zh", "target_lang": "en", "model": "nllb-200-distilled-600m"}'
```

### 模型列表

```bash
curl http://localhost:8000/v1/models
```

## 模型能力矩阵

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

## WebUI 功能

### 离线识别
- 单文件处理：上传音频/视频，支持多种格式，实时预览和下载
- 批量处理：批量上传文件，一键生成识别结果

### 流式识别
- 文件流式识别：边上传边识别，实时输出结果
- 麦克风实时识别：浏览器麦克风实时采集，流式输出识别文本

### 说话人分离
- 自动识别不同说话人，按说话人分段输出

### 情感识别
- 识别音频中的情感标签

### 文本翻译
- 支持 200+ 语言互译
- 基于 NLLB 模型

### 服务与调试
- 模型列表管理，查看下载状态和加载状态
- 运行日志查看

## 测试

```bash
# 运行全量测试
python -m pytest tests/ -x -q

# 运行 WebUI 测试
python -m pytest tests/test_pat_webui_diarization_exports.py -x -q

# 运行工具函数测试
python -m pytest tests/test_pat_webui_utils.py -x -q
```

## 文档

- [模型能力矩阵](Docs/model-capability-matrix.md)：模型语言覆盖、能力差异与 API 参数说明
- [API 文档](Docs/api.md)：后端 API 端点、参数、响应格式
- [部署指南](Docs/deployment.md)：本地运行与发布建议
- [运行环境](Docs/requirements.md)：OS/GPU/Python/端口/环境变量约束
- [设计文档](Docs/design.md)：整体架构与关键数据流
- [变更记录](Docs/changelog.md)：版本变更历史
- [优化计划](Docs/optimization-plan.md)：性能与稳定性优化计划

## 外部参考

- [FunASR 官方教程](https://modelscope.github.io/FunASR/zh/tutorial.html)
- [FunASR API 文档](https://modelscope.github.io/FunASR/api.html)
- [FunASR GitHub](https://github.com/modelscope/FunASR)

## 许可证

本项目基于 FunASR 封装，请遵守 FunASR 的许可协议。
