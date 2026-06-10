#
ASR 模型能力矩阵与 API 参数说明

目的：让你快速理解“不同模型能做到什么/不能做到什么”，以及在本项目里“可调用的 API、可用参数、可得到的输出格式”分别是什么。

范围：

- 当前项目已接入的 OpenAI 兼容 API（FastAPI）："app/openai_api/server.py"
- 当前 API 已内置的模型别名（model 参数）：sensevoice / paraformer / paraformer-en / fun-asr-nano / qwen3-asr / qwen3-asr-0.6b
- 输出格式：json / verbose_json / txt / srt / vtt / tsv / all(zip)

## 命名说明（重要）

为避免把“模型系列名称”与“model id/别名”混淆，统一口径如下：

- 官方 Fun-ASR-Nano：通常指 "FunAudioLLM/Fun-ASR-Nano-2512"
- 官方 Qwen3-ASR：示例常用 "Qwen/Qwen3-ASR-1.7B"
- 本项目：提供别名 "fun-asr-nano"（对应 Fun-ASR-Nano-2512）与 "qwen3-asr"（对应 Qwen3-ASR-1.7B）；另提供 "qwen3-asr-0.6b" 作为更轻量的可选项

## 一、先讲结论：两层能力

- 模型无关（保证可实现）：输出格式（json/txt/srt/vtt/tsv/zip）、分行/断句规则、编码、打包、API 约定、参数校验白名单
- 模型相关（取决于模型与链路）：标点质量、时间戳精度（无/段级/字词级）、热词效果、语言识别、说话人相关信息、流式能力

## 二、当前项目：API 能力与参数（现状）

API 实现：[server.py](../app/openai_api/server.py)

### 1) 端点

- POST "/v1/audio/transcriptions"
- GET "/v1/models"
- GET "/health"

### 2) POST /v1/audio/transcriptions（已实现参数）

- file：音频文件（必填）
- model：模型别名（默认 "sensevoice"）
  - 可选：sensevoice / paraformer / paraformer-en / fun-asr-nano / qwen3-asr / qwen3-asr-0.6b
- language：语言提示（可选，透传给 FunASR generate）
- response_format：输出格式（默认 "json"）
  - 已实现：json / verbose_json / txt / srt / vtt / tsv / all(zip)
- max_line_width：每行最大字符数（可选，仅影响 txt/srt/vtt 渲染）
- vad_preset：default / anti_hallucination（可选）
- merge_vad：true/false（可选）
- merge_length_s：合并段长度（秒，可选，需要 merge_vad=true）

### 3) 输出字段（已实现）

- json：{"text": "..."}
- verbose_json：{"text","segments","language","duration","model"}
  - segments 仅在模型返回 sentence_info 时生成（否则为空）

## 三、模型能力矩阵（以“能否稳定提供”为准）

说明：

- “✅”表示在设计上可依赖（作为主路径）
- “⚠️”表示可能可用但不稳定/依赖具体版本或配置（需要回退逻辑）
- “❌”表示不应指望模型直接提供（需后处理或额外模型链路）

| 模型别名 | 本项目 model id | 官方文档常见对应 | hub | 语言覆盖 | 标点 | 句/段级时间戳 | sentence_info 分段 | 热词 | 典型用途 |
|---|---|---|---|---|---|---|---|---|---|
| sensevoice | iic/SenseVoiceSmall | iic/SenseVoiceSmall | ms | 明确列出：普通话、粤语、英语、日语、韩语；README 示例语言码：`auto / zh / en / yue / ja / ko / nospeech`；正文另称总体支持 50+ 语种，但未公开完整名单 | ⚠️（建议 use_itn + 后处理） | ⚠️（常见做法：用 VAD 段当“段级时间戳”） | ⚠️ | ⚠️ | 通用转写、文案；可做“粗字幕” |
| paraformer | paraformer-zh | paraformer-zh | ms | 官方 Model Zoo：中文和英文；未见官方单独列出方言清单 | ✅（已配置 ct-punc） | ✅/⚠️（上游文档描述“带时间戳输出”，仍需实测字段形态） | ⚠️（若开启 sentence_timestamp 且具备 timestamp+punc_array） | ✅（hotword） | 中文字幕/文案（可读性优先） |
| paraformer-en | paraformer-en | paraformer-en | ms | 官方 Model Zoo：英文；未见官方单独列出方言/口音清单 | ⚠️（可接 punc_model，但当前配置未启用） | ⚠️ | ⚠️ | ⚠️ | 英文转写 |
| paraformer-zh-streaming | paraformer-zh-streaming | iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online | ms | 官方 Model Zoo：中文和英文；未见官方单独列出方言清单 | ✅（默认挂 ct-punc） | ⚠️（当前项目流式页不返回时间戳） | ❌ | ⚠️ | 低延迟流式识别 |
| fun-asr-nano | FunAudioLLM/Fun-ASR-Nano-2512 | FunAudioLLM/Fun-ASR-Nano-2512 | ms | 中文、英文、日文；中文明确包含 7 种方言：吴语、粤语、闽语、客家话、赣语、湘语、晋语；官方另称支持 26 种地域口音，README 当前公开列举样例包括河南、陕西、湖北、四川、重庆、云南、贵州、广东、广西、河北、天津、山东、安徽、南京、江苏、杭州、甘肃、宁夏 | ⚠️ | ⚠️ | ⚠️ | ⚠️ | 通用 ASR、方言、歌词、说话人分离 |
| qwen3-asr | Qwen/Qwen3-ASR-1.7B | Qwen/Qwen3-ASR-1.7B | ms | 30 种语言：中文、英语、粤语、阿拉伯语、德语、法语、西班牙语、葡萄牙语、印尼语、意大利语、韩语、俄语、泰语、越南语、日语、土耳其语、印地语、马来语、荷兰语、瑞典语、丹麦语、芬兰语、波兰语、捷克语、菲律宾语、波斯语、希腊语、匈牙利语、马其顿语、罗马尼亚语；22 种中文方言/口音：安徽、东北、福建、甘肃、贵州、河北、河南、湖北、湖南、江西、宁夏、山东、陕西、山西、四川、天津、云南、浙江、粤语（香港口音）、粤语（广东口音）、吴语、闽南语 | ⚠️ | ⚠️ | ⚠️ | ⚠️ | 最高质量离线识别（当前项目未接其原生 streaming/vLLM 链路） |
| qwen3-asr-0.6b | Qwen/Qwen3-ASR-0.6B | Qwen/Qwen3-ASR-0.6B | ms | 与 `qwen3-asr` 相同：30 种语言 + 上述 22 种中文方言/口音 | ⚠️ | ⚠️ | ⚠️ | ⚠️ | 更轻量的 Qwen3-ASR（当前项目未接其原生 streaming/vLLM 链路） |
| emotion2vec-plus-large | iic/emotion2vec_plus_large | iic/emotion2vec_plus_large | ms | 官方 README 强调跨语种/跨场景鲁棒性，但未公开逐项语言与中文方言名单 | ❌ | ❌ | ❌ | ❌ | 独立情感识别 |

流式模型候选说明：

- 当前项目已启用：`paraformer-zh-streaming`
- 官方/Model Zoo 还有 Online/Streaming 候选：Paraformer-online、Paraformer-large-online、UniASR online 多语种系列
- 本轮策略：只记录候选，不自动下载、不默认启用；新增模型前需要先确认模型缓存、依赖、显存与输出协议

补充：本项目在 MODEL_CONFIGS 中的链路配置

- sensevoice：vad_model=fsmn-vad，vad_kwargs.max_single_segment_time=30000；官方链路原生带标点，当前项目不默认挂外置 punc_model
- paraformer：vad_model=fsmn-vad，punc_model=ct-punc
- fun-asr-nano：hub=ms，trust_remote_code=False，vad_model=fsmn-vad
- qwen3-asr：hub=ms，trust_remote_code=False，dtype=fp16，vad_model=fsmn-vad
- qwen3-asr-0.6b：hub=ms，trust_remote_code=False，dtype=fp16，vad_model=fsmn-vad

语言口径补充说明：

- `sensevoice`：官方公开资料能明确点名的是普通话、粤语、英语、日语、韩语；“50+ 语种”属于总体能力口径，当前未见完整逐项清单。
- `paraformer` 系：这里写的是当前项目别名实际映射到的具体官方模型语言口径，不等于整个 Paraformer 家族所有子模型的语言范围。
- `fun-asr-nano`：官方 README_zh 同时出现“覆盖 31 个语种”与单模型表格“中文 / 英文 / 日文”两层口径；当前项目接的是 `Fun-ASR-Nano-2512`，所以表格按该单模型的明确公开列表写，同时保留其中文 7 方言与 26 地域口音说明。
- `qwen3-asr` 系：30 种语言和 22 种中文方言/口音是官方 README 直接给出的完整清单；但当前项目只接入离线路径，不能因为上游支持 streaming 就在本项目文档中默认宣称可直接流式使用。
- `emotion2vec-plus-large`：官方公开文档强调“跨语种与跨场景鲁棒性”，但未给出可核对的逐项语种/方言表，因此这里只能如实保留“未公开枚举”。

官方来源：

- SenseVoice README：[https://github.com/FunAudioLLM/SenseVoice/blob/main/README.md](https://github.com/FunAudioLLM/SenseVoice/blob/main/README.md)
- FunASR Model Zoo：[https://raw.githubusercontent.com/modelscope/FunASR/main/model_zoo/modelscope_models_zh.md](https://raw.githubusercontent.com/modelscope/FunASR/main/model_zoo/modelscope_models_zh.md)
- Fun-ASR README_zh：[https://github.com/FunAudioLLM/Fun-ASR/blob/main/README_zh.md](https://github.com/FunAudioLLM/Fun-ASR/blob/main/README_zh.md)
- Qwen3-ASR README：[https://github.com/QwenLM/Qwen3-ASR/blob/main/README.md](https://github.com/QwenLM/Qwen3-ASR/blob/main/README.md)

## 四、功能实现路径（按能力来源拆解）

### 1) 标点

- 主路径（paraformer）：punc_model=ct-punc → 输出 text 自带标点
- SenseVoice：官方链路原生带标点；本项目优先 use_itn 与输出清洗，不默认挂外置 PUNC

### 2) 句/段级时间戳（你已选择“句/段级”）

可用的三种来源（优先级建议）：

1. 模型原生 timestamp/sentence_info（若存在）✅
2. VAD 切分段（beg/end）作为段级时间戳 ✅（粗但稳定）
3. 额外对齐模型（fa-zh，输入 wav+text）→ 字级时间戳 → 聚合成句/段 ⚠️（链路更复杂，本次先不作为必需）

### 3) 输出格式（txt/json/srt/vtt/tsv/all）

- 单格式：由“输出渲染器”生成，独立于模型
- all：按你要求，“每种格式不一样”，规划为 zip（包含多个文件）

对应升级策划文档：

- [upgrade-plan-output-template.md](./upgrade-plan-output-template.md)

## 五、参数与功能的“白名单”建议（规划）

原因：不同模型支持的 kwargs 不同，且直接开放任意 kwargs 会引入不可控行为与安全风险。

建议 API 只开放：

- 通用：model / language / response_format
- 字幕渲染：max_line_width / max_words_per_line / max_line_count
- VAD：vad_preset / merge_vad / merge_length_s（vad_kwargs 仅开放白名单字段）
- 文本：use_itn（仅 sensevoice）/ hotword（仅 paraformer 优先）

## 六、你关心的“输出能力差异”怎么落地成产品体验

- UI/客户端层面：展示“模型能力提示卡”
  - sensevoice：通用转写与情感/说话人入口集中；语言码以 `auto / zh / en / yue / ja / ko / nospeech` 为主
  - paraformer：中文字幕强；标点强；时间戳更有希望
  - qwen3-asr：明确标注“当前项目只接离线，不代表已接原生 streaming”
- API 层面：对不同模型做参数兼容性校验
  - 例如：对 sensevoice 提示 use_itn；对 paraformer 提示 punc/hotword；对无时间戳模型默认走 VAD 段时间戳
