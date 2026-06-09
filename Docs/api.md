#
OpenAI 兼容 API 说明（API）

实现文件：["app/openai_api/server.py"](../app/openai_api/server.py)

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
  "models_available": [
    "sensevoice",
    "paraformer",
    "paraformer-en",
    "paraformer-zh-streaming",
    "fun-asr-nano",
    "qwen3-asr",
    "qwen3-asr-0.6b",
    "emotion2vec-plus-large"
  ]
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
- "vad_max_single_segment_time"（可选）：VAD 单段最大时长，单位毫秒（ms）
- "merge_vad"（可选）：true/false（优先级高于 vad_preset）
- "merge_length_s"（可选）：合并段长度（秒，需要 merge_vad=true）
- "hotword"（可选）：热词字符串（逗号/空格分隔）
- "use_itn"（可选）：是否开启逆文本正规化
- "batch_size_s"（可选）：动态批总时长，单位秒（s）
- "batch_size_threshold_s"（可选）：动态批阈值，单位秒（s），用于降低长音频 OOM 风险
- "punc_mode"（可选，默认 "auto"）
  - "auto"：按模型默认配置处理标点
  - "disabled"：关闭外置 PUNC 模型（当前主要作用于 paraformer）
- "device"（可选）：本次请求的运行设备，如 "cuda" / "cpu"
- "hub"（可选）：模型仓库来源，如 "ms" / "hf"
- "disable_update"（可选）：是否关闭在线更新检查
- "ncpu"（可选）：CPU 线程数
- "log_level"（可选）：日志级别，可选 "DEBUG" / "INFO" / "WARNING" / "ERROR" / "CRITICAL"
- "disable_pbar"（可选）：是否关闭进度条

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

补充说明：

- `"/v1/models"` 的 `"ready"` 按“模型主名 + 任一已加载运行时变体”判断，因此即使某模型以自定义 `device / ncpu / punc_mode` 变体加载，前端仍会把该模型视为已就绪

### POST "/v1/funasr/streaming"

用途：流式识别（按分片多次调用，服务端维护 session cache）。

请求类型：multipart/form-data

表单字段：

- "file"（必填）：PCM 分片数据（建议 "s16le" / 16k / mono / int16）
- "model"（可选，默认 "paraformer-zh-streaming"）：流式模型名
- "session_id"（可选）：会话 ID；首次可不填（服务端会生成并返回）
- "reset"（可选，默认 false）：是否重置该 session 的 cache
- "is_final"（可选，默认 false）：最后一个分片标记
- "chunk_size"（可选，默认 "0,10,5"）：Paraformer-Streaming 的 chunk 配置
- "encoder_chunk_look_back"（可选，默认 0）
- "decoder_chunk_look_back"（可选，默认 0）

响应示例：

```json
{
  "session_id": "a1b2c3...",
  "text": "本分片增量文本",
  "full_text": "累计文本",
  "is_final": false
}
```

导出说明：

- 当结果段中包含 `"speaker"` 时，`txt / srt / vtt / tsv / all(zip)` 的文本内容会自动追加前缀，例如 `"[spk=0] 你好"`

错误约定：

- 参数错误：HTTP 400
- 推理/加载异常：HTTP 500（detail 为异常信息字符串）

### POST "/v1/funasr/emotion"

用途：情感识别（当前先支持整体情感排序，后续再增强时间片能力）。

请求类型：multipart/form-data

表单字段：

- "file"（必填）：音频文件或从视频抽出的音频
- "model"（可选，默认 "emotion2vec-plus-large"）：情感模型名
- "granularity"（可选，默认 "utterance"）
  - "utterance"：整段情感排序
  - "frame"：按帧粒度提取内部特征，当前仍返回整段情感排序

响应示例：

```json
{
  "model": "emotion2vec-plus-large",
  "granularity": "utterance",
  "top_emotion": "happy",
  "top_score": 0.7,
  "emotions": [
    {"label": "happy", "score": 0.7},
    {"label": "neutral", "score": 0.2},
    {"label": "sad", "score": 0.1}
  ]
}
```

错误约定：

- 参数错误：HTTP 400
- 推理/加载异常：HTTP 500（detail 为异常信息字符串）

### POST "/v1/funasr/diarization"

用途：说话人分离（当前 MVP 先支持 `paraformer + cam++`）。

请求类型：multipart/form-data

表单字段：

- "file"（必填）：音频文件或从视频抽出的音频
- "model"（可选，默认 "paraformer"）：当前仅支持 `"paraformer"`
- "spk_model"（可选，默认 "cam++"）
- "spk_mode"（可选，默认 "punc_segment"）
  - "default"
  - "vad_segment"
  - "punc_segment"
- "preset_spk_num"（可选）：已知说话人数

响应示例：

```json
{
  "text": "你好 欢迎光临",
  "segments": [
    {"start": 0.0, "end": 1.2, "text": "你好", "speaker": 0},
    {"start": 1.2, "end": 2.8, "text": "欢迎光临", "speaker": 1}
  ],
  "speakers": [0, 1],
  "model": "paraformer",
  "spk_model": "cam++",
  "spk_mode": "punc_segment",
  "duration": 2.8
}
```

错误约定：

- 参数错误：HTTP 400
- 推理/加载异常：HTTP 500（detail 为异常信息字符串）

## 模型配置（静态配置）

"MODEL_CONFIGS" 内置可用模型：

- 统一策略：所有模型 `hub="ms"`（ModelScope），并设置 `disable_update=True`（关闭在线更新检查）
- 加载策略：服务启动时不预加载模型；首次请求对应能力时才按需加载，`GET "/v1/models"` 中的 `ready` 仅表示当前进程是否已缓存该模型

- "sensevoice"
  - 模型："iic/SenseVoiceSmall"
  - VAD："fsmn-vad"
  - VAD 参数：{"max_single_segment_time": 30000}
  - 标点：官方链路原生带标点，当前项目不默认挂外置 `punc_model`
  - 语言口径：官方明确列出普通话、粤语、英语、日语、韩语；README 示例语言码为 `auto / zh / en / yue / ja / ko / nospeech`；正文另称总体支持 50+ 语种，但未公开完整名单
- "paraformer"
  - 模型："paraformer-zh"
  - VAD："fsmn-vad"
  - 标点："ct-punc"
  - 语言口径：官方 Model Zoo 标注为中文和英文；未见官方单独列出方言清单
- "paraformer-en"
  - 模型："paraformer-en"
  - VAD："fsmn-vad"
  - 语言口径：官方 Model Zoo 标注为英文；未见官方单独列出方言/口音清单
- "paraformer-zh-streaming"
  - 模型："paraformer-zh-streaming"
  - 说明：流式模型，不走 VAD/离线分段
  - 语言口径：官方 Model Zoo 标注为中文和英文；未见官方单独列出方言清单
- "fun-asr-nano"
  - 模型："FunAudioLLM/Fun-ASR-Nano-2512"（hub=ms，trust_remote_code=False）
  - VAD："fsmn-vad"
  - 语言口径：中文、英文、日文；中文明确包含 7 种方言：吴语、粤语、闽语、客家话、赣语、湘语、晋语；官方另称支持 26 种地域口音，README 当前公开列举样例包括河南、陕西、湖北、四川、重庆、云南、贵州、广东、广西、河北、天津、山东、安徽、南京、江苏、杭州、甘肃、宁夏

补充：

- "qwen3-asr"
  - 模型："Qwen/Qwen3-ASR-1.7B"（hub=ms，trust_remote_code=False）
  - VAD："fsmn-vad"
  - DType："fp16"
  - 语言口径：30 种语言：中文、英语、粤语、阿拉伯语、德语、法语、西班牙语、葡萄牙语、印尼语、意大利语、韩语、俄语、泰语、越南语、日语、土耳其语、印地语、马来语、荷兰语、瑞典语、丹麦语、芬兰语、波兰语、捷克语、菲律宾语、波斯语、希腊语、匈牙利语、马其顿语、罗马尼亚语；22 种中文方言/口音：安徽、东北、福建、甘肃、贵州、河北、河南、湖北、湖南、江西、宁夏、山东、陕西、山西、四川、天津、云南、浙江、粤语（香港口音）、粤语（广东口音）、吴语、闽南语
  - 说明：当前项目只接入离线路径，未接入其原生 streaming / vLLM / qwen-asr 工具链
- "qwen3-asr-0.6b"
  - 模型："Qwen/Qwen3-ASR-0.6B"（hub=ms，trust_remote_code=False）
  - VAD："fsmn-vad"
  - DType："fp16"
  - 语言口径：与 "qwen3-asr" 相同：30 种语言 + 上述 22 种中文方言/口音
  - 说明：当前项目只接入离线路径，未接入其原生 streaming / vLLM / qwen-asr 工具链
- "emotion2vec-plus-large"
  - 模型："iic/emotion2vec_plus_large"
  - 说明：独立情感识别模型；官方 README 强调跨语种/跨场景鲁棒性，但未枚举具体语种与中文方言

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
