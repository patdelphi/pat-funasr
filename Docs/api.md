﻿﻿#
OpenAI 兼容 API 说明（API）

实现文件：["app/openai_api/server.py"](file:///y:/NewStore/AI/FunASR-Portable-GPU/app/openai_api/server.py)

## 基础信息

- Base URL：默认 "http://localhost:8000"
- OpenAPI 文档（FastAPI）："/docs"

## 端点一览

### GET "/health"

用途：健康检查与运行状态。

响应示例：

```json
{
  "status": "ok",
  "device": "cuda",
  "models_loaded": ["sensevoice"],
  "models_available": ["sensevoice", "paraformer", "paraformer-en", "fun-asr-nano"]
}
```

### GET "/v1/models"

用途：OpenAI 风格的模型列表。

字段说明：

- "id"：模型名（提交转写时的 form 字段 "model"）
- "ready"：是否已在当前进程内加载完成

### POST "/v1/audio/transcriptions"

用途：OpenAI 兼容的音频转写接口（与 OpenAI "/v1/audio/transcriptions" 形态一致）。

请求类型：multipart/form-data

表单字段：

- "file"（必填）：音频文件（wav/mp3/flac/m4a/ogg/webm 等）
- "model"（可选，默认 "sensevoice"）：模型名
- "language"（可选）：语言提示（透传给 FunASR 的 generate）
- "response_format"（可选，默认 "json"）：输出格式
  - "json"：只返回 {"text"}
  - "verbose_json"：返回 {"text","segments",...}
  - "txt"：纯文本（分段分行）
  - "srt"：SRT 字幕
  - "vtt"：VTT 字幕
  - "tsv"：TSV（start/end/text）
  - "all"：zip 打包（含 txt/json/srt/vtt/tsv）
- "max_line_width"（可选）：每行最大字符数（仅影响 txt/srt/vtt 渲染）
- "vad_preset"（可选）：VAD 预设
  - "default"
  - "anti_hallucination"：更激进过滤静音/噪声段
- "merge_vad"（可选）：true/false（优先级高于 vad_preset）
- "merge_length_s"（可选）：合并段长度（秒，需要 merge_vad=true）

响应：

- "response_format=json"
  - {"text": "<识别文本>"}
- "response_format=verbose_json"
  - {"text","segments","language","duration","model"}
- "response_format=txt"
  - text/plain
- "response_format=srt"
  - application/x-subrip
- "response_format=vtt"
  - text/vtt
- "response_format=tsv"
  - text/tab-separated-values
- "response_format=all"
  - application/zip（Content-Disposition: attachment; filename="output.zip"）

错误约定：

- 参数错误（例如 model 不存在）：HTTP 400
- 推理/加载异常：HTTP 500（detail 为异常信息字符串）

## 模型配置（静态配置）

"MODEL_CONFIGS" 内置可用模型：

- 统一策略：所有模型 `hub="ms"`（ModelScope），并设置 `disable_update=True`（关闭在线更新检查）

- "sensevoice"
  - 模型："iic/SenseVoiceSmall"
  - VAD："fsmn-vad"
  - VAD 参数：{"max_single_segment_time": 30000}
- "paraformer"
  - 模型："paraformer-zh"
  - VAD："fsmn-vad"
  - 标点："ct-punc"
- "paraformer-en"
  - 模型："paraformer-en"
  - VAD："fsmn-vad"
- "fun-asr-nano"
  - 模型："FunAudioLLM/Fun-ASR-Nano-2512"（hub=ms，trust_remote_code=True）
  - VAD："fsmn-vad"

补充：

- "qwen3-asr"
  - 模型："Qwen/Qwen3-ASR-1.7B"（hub=ms，trust_remote_code=True）
  - VAD："fsmn-vad"
- "qwen3-asr-0.6b"
  - 模型："Qwen/Qwen3-ASR-0.6B"（hub=ms，trust_remote_code=True）
  - VAD："fsmn-vad"

## 清洗规则（SenseVoice 输出）

服务会对输出做清洗：

- 删除 "<|...|>" 形态的特殊标记
- 删除残留的 "<...>" 形态 token
- 合并多空格与控制字符

## 分段与时间戳（字幕/TSV）

说明：本项目对不同模型的时间戳能力做了“主路径 + 兜底”。

- 主路径：优先使用模型输出中的 "sentence_info"（若存在）来构建 "segments" 并渲染为 SRT/VTT/TSV
- 兜底：当模型不返回 "sentence_info" 时，会用 ffprobe 获取音频时长，并按标点/长度对文本切分为多段，避免字幕只有“一整句/整段”

## 跑批测试（test 目录）

用途：遍历 "test\\" 下音视频文件，输出 ASR 结果到各模型目录（每个模型一个目录）。

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File ".\run_test_all_models.ps1"
```

输出目录：

- "test\\sensevoice\\"
- "test\\paraformer\\"
- "test\\fun-asr-nano\\"

说明：

- 跑批脚本默认不跑 "paraformer-en"（如需英文模型，可自行调用 "scripts/batch_transcribe.py" 指定 "--model-alias paraformer-en"）
