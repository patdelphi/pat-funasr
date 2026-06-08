#
ASR 模型能力矩阵与 API 参数说明

目的：让你快速理解“不同模型能做到什么/不能做到什么”，以及在本项目里“可调用的 API、可用参数、可得到的输出格式”分别是什么。

范围：

- 当前项目已接入的 OpenAI 兼容 API（FastAPI）："app/openai_api/server.py"
- 当前 API 已内置的模型别名（model 参数）：sensevoice / paraformer / paraformer-en / fun-asr-nano
- 输出格式：当前已实现（json/verbose_json）+ 已规划（txt/srt/vtt/tsv/all-zip，见升级策划文档）

## 一、先讲结论：两层能力

- 模型无关（保证可实现）：输出格式（json/txt/srt/vtt/tsv/zip）、分行/断句规则、编码、打包、API 约定、参数校验白名单
- 模型相关（取决于模型与链路）：标点质量、时间戳精度（无/段级/字词级）、热词效果、语言识别、说话人相关信息、流式能力

## 二、当前项目：API 能力与参数（现状）

API 实现：[server.py](file:///y:/NewStore/AI/FunASR-Portable-GPU/app/openai_api/server.py)

### 1) 端点

- POST "/v1/audio/transcriptions"
- GET "/v1/models"
- GET "/health"

### 2) POST /v1/audio/transcriptions（已实现参数）

- file：音频文件（必填）
- model：模型别名（默认 "sensevoice"）
  - 可选：sensevoice / paraformer / paraformer-en / fun-asr-nano
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

| 模型别名 | 上游 model id | hub | 语言覆盖 | 标点 | 句/段级时间戳 | sentence_info 分段 | 热词 | 典型用途 |
|---|---|---|---|---|---|---|---|---|
| sensevoice | iic/SenseVoiceSmall | ms | 多语（上游示例支持 auto/zh/en/yue/ja/ko…） | ⚠️（建议 use_itn + 后处理） | ⚠️（常见做法：用 VAD 段当“段级时间戳”） | ⚠️ | ⚠️ | 多语通用转写、文案；可做“粗字幕” |
| paraformer | paraformer-zh | ms/hf | 中文 | ✅（已配置 ct-punc） | ✅/⚠️（上游文档描述“带时间戳输出”，仍需实测字段形态） | ⚠️（若开启 sentence_timestamp 且具备 timestamp+punc_array） | ✅（hotword） | 中文字幕/文案（可读性优先） |
| paraformer-en | paraformer-en | ms/hf | 英文 | ⚠️（可接 punc_model，但当前配置未启用） | ⚠️ | ⚠️ | ⚠️ | 英文转写 |
| fun-asr-nano | FunAudioLLM/Fun-ASR-Nano-2512 | hf | 多语 | ⚠️（模型输出可能带标点，但不作为强保证） | ⚠️ | ⚠️ | ⚠️ | 低延迟/生态相关（vLLM/批处理等） |

补充：本项目在 MODEL_CONFIGS 中的链路配置

- sensevoice：vad_model=fsmn-vad，vad_kwargs.max_single_segment_time=30000
- paraformer：vad_model=fsmn-vad，punc_model=ct-punc
- fun-asr-nano：hub=hf，trust_remote_code=True，vad_model=fsmn-vad

## 四、功能实现路径（按能力来源拆解）

### 1) 标点

- 主路径（paraformer）：punc_model=ct-punc → 输出 text 自带标点
- SenseVoice：优先 use_itn；否则只能后处理（规则断句/标点模型等）

### 2) 句/段级时间戳（你已选择“句/段级”）

可用的三种来源（优先级建议）：

1. 模型原生 timestamp/sentence_info（若存在）✅
2. VAD 切分段（beg/end）作为段级时间戳 ✅（粗但稳定）
3. 额外对齐模型（fa-zh，输入 wav+text）→ 字级时间戳 → 聚合成句/段 ⚠️（链路更复杂，本次先不作为必需）

### 3) 输出格式（txt/json/srt/vtt/tsv/all）

- 单格式：由“输出渲染器”生成，独立于模型
- all：按你要求，“每种格式不一样”，规划为 zip（包含多个文件）

对应升级策划文档：

- [upgrade-plan-output-template.md](file:///y:/NewStore/AI/FunASR-Portable-GPU/Docs/upgrade-plan-output-template.md)

## 五、参数与功能的“白名单”建议（规划）

原因：不同模型支持的 kwargs 不同，且直接开放任意 kwargs 会引入不可控行为与安全风险。

建议 API 只开放：

- 通用：model / language / response_format
- 字幕渲染：max_line_width / max_words_per_line / max_line_count
- VAD：vad_preset / merge_vad / merge_length_s（vad_kwargs 仅开放白名单字段）
- 文本：use_itn（仅 sensevoice）/ hotword（仅 paraformer 优先）

## 六、你关心的“输出能力差异”怎么落地成产品体验

- UI/客户端层面：展示“模型能力提示卡”
  - sensevoice：多语、文案强；字幕时间戳以段级为主
  - paraformer：中文字幕强；标点强；时间戳更有希望
- API 层面：对不同模型做参数兼容性校验
  - 例如：对 sensevoice 提示 use_itn；对 paraformer 提示 punc/hotword；对无时间戳模型默认走 VAD 段时间戳
