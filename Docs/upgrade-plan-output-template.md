#
FunASR-Portable-GPU 输出模板/字幕升级策划（参考 Whisper-CTranslate2 参数体系）

目标：参考 "Whisper-CTranslate2" 的参数组织方式（输出格式、VAD、提示词/热词、分行规则等），在当前项目中形成一套“可控输出模板”的统一方案（先出计划，确认后再实施）。

## 参考资料

- FunASR 官方中文文档（上游）：https://github.com/modelscope/FunASR/blob/main/README_zh.md
- 本仓库 API：["app/openai_api/server.py"](file:///y:/NewStore/AI/FunASR-Portable-GPU/app/openai_api/server.py)
- Whisper-CTranslate2 本地参考：
  - CLI 参数总览：["Whisper-CTranslate2/readme.txt"](file:///y:/NewStore/AI/FunASR-Portable-GPU/Whisper-CTranslate2/readme.txt)
  - 批处理示例：["Whisper-CTranslate2/batchwhisper.bat"](file:///y:/NewStore/AI/FunASR-Portable-GPU/Whisper-CTranslate2/batchwhisper.bat)

## 现状与差距（面向“字幕/文案输出”）

- 现状：当前 API 仅提供 "json"/"verbose_json" 两种响应结构，且分段/时间戳依赖模型能力与 FunASR 内部结果字段。
- 目标：对齐 Whisper-CTranslate2 的体验：
  - "output_format=txt/json/srt/vtt/tsv/all"
  - VAD 过滤（“消除幻听”）
  - 初始提示词/热词
  - 字幕分行控制（max_line_width/max_line_count/max_words_per_line）
  - 时间戳（至少 sentence-level；条件允许时 word-level）

## 上游 FunASR 可用能力清单（与本需求强相关）

来自上游 README_zh 的关键点（只摘与本次升级直接相关的）：

- VAD：使用 "fsmn-vad" 进行长音频切割，输出为毫秒级片段列表 `[[beg_ms, end_ms], ...]`。
- SenseVoice 推理常用参数：
  - "language"（auto/zh/en/yue/ja/ko/nospeech）
  - "use_itn"（输出是否包含标点与逆文本正则化）
  - "batch_size_s"（动态 batch，总音频时长秒数）
  - "merge_vad"+"merge_length_s"（合并 VAD 切碎片，控制分段长度）
  - 建议用 "rich_transcription_postprocess" 做后处理（用于清理/规范化输出）
- Paraformer 推理常用参数：
  - "punc_model=ct-punc"（标点恢复）
  - "hotword"（热词）
  - 文档中明确：paraformer-zh “带时间戳输出”
- “时间戳预测”独立模型：
  - "fa-zh"：输入 (wav, text) 预测字级别时间戳（这为“给不带时间戳的 ASR 结果补时间戳”提供了可行路径）

## Whisper-CTranslate2 参数 → FunASR 能力映射（策划版）

| Whisper-CTranslate2 | 作用 | FunASR 对应点（上游能力/可实现方式） | 备注 |
|---|---|---|---|
| "--output_format {txt,vtt,srt,tsv,json,all}" | 输出模板 | 在 API 增加 "response_format" 扩展：txt/srt/vtt/tsv/json/verbose_json/all | "all" 需要定义返回协议 |
| "--vad_filter True" | 过滤无语音片段（降幻听） | "vad_model=fsmn-vad"；结合 "vad_kwargs" + "merge_vad" 控制切分与合并 | “消除幻听版”可等价为强制开启 VAD + 更激进阈值 |
| "--vad_*" 参数 | VAD 阈值/最短语音/静音等 | 扩展 "vad_kwargs" 暴露到 API（白名单字段） | 需确认 FunASR fsmn-vad 支持的具体字段 |
| "--initial_prompt" | 初始提示词 | FunASR 有 "hotword"；另可在上层做“提示词模板”（对输出后处理，不直接喂模型） | SenseVoice/Paraformer 对 prompt 支持弱于 Whisper；优先用 hotword |
| "--hotwords" | 热词 | "hotword"（Paraformer 示例已出现） | 可设计为逗号分隔/多值 |
| "--max_line_width/--max_words_per_line/--max_line_count" | 字幕断行 | 在“字幕渲染器”阶段实现（不依赖模型） | 这部分应完全后处理实现 |
| "--word_timestamps True" | 词级时间戳 | 优先：模型原生 timestamp；备选：使用 "fa-zh" 补字级时间戳，再聚合成词/句 | 句级先做，词级作为后续增强 |
| "--hallucination_silence_threshold" | 幻听抑制 | 通过 VAD 切分 + 丢弃过短片段/过长静音 | 先以 VAD 规则实现 |

## 统一输出架构提案（核心）

### 1) “输出渲染器”层（强建议独立于模型）

定义一个纯后处理模块（不依赖具体模型），输入统一结构：

- "full_text"：完整文本（带/不带标点均可）
- "segments"：分段列表（每段至少有 text；尽量有 start/end 秒）
- "meta"：语言、模型名、device、耗时、是否 use_itn、VAD 切分信息

渲染器负责生成：

- "txt"：带分行规则的文案
- "json"：结构化段落
- "srt"/"vtt"：字幕
- "tsv"：可选导出（对齐 whisper）
- "all"：一次性返回多个格式（见下一节协议）

### 2) “all” 的返回协议（两种可选方案）

方案 A：返回 JSON，内含多格式文本（偏“API 调用”）

- Content-Type：application/json
- 返回结构示例：
  - text：纯文本（兼容 OpenAI）
  - formats：{ "txt": "...", "srt": "...", "vtt": "...", "tsv": "...", "json": {...} }

方案 B（推荐）：返回 zip 文件（更像 CLI 工具，也最符合“每种格式本来就不一样”的直觉）

- Content-Type：application/zip
- 内含：output.txt/output.srt/output.vtt/output.json/output.tsv
- 优点：易落盘；缺点：不再是 OpenAI 兼容的 JSON 响应

建议：

- "response_format=txt/json/verbose_json/srt/vtt/tsv"：单格式直出
- "response_format=all"：默认走 zip（方案 B）
- 如确实需要 “JSON 内含多格式”（方案 A），可额外提供一个明确值（例如 "response_format=all_json"），避免语义混淆

### 3) 单格式返回的 Content-Type 约定（规划）

- "txt"：text/plain; charset=utf-8
- "srt"：application/x-subrip; charset=utf-8
- "vtt"：text/vtt; charset=utf-8
- "tsv"：text/tab-separated-values; charset=utf-8
- "json"/"verbose_json"：application/json
- "all"(zip)：application/zip

## 分模型策略（“可选，都要”）

### SenseVoice（默认推荐：长音频文案 + 基础字幕）

- 重点用上游参数：
  - "use_itn=True"：争取标点/规范化输出
  - "merge_vad=True"+"merge_length_s=15"：让段落更像字幕分段
  - "batch_size_s"：大音频提升吞吐
- 时间戳策略：
  - 若原生不提供 timestamp：用 VAD 分段的 (beg/end) 作为段级时间戳（“粗字幕”）
  - 若要更精细：引入 "fa-zh"（wav+text）补“字级时间戳”，再聚合为句级/词级

### Paraformer（默认推荐：字幕）

- 重点用上游参数：
  - "punc_model=ct-punc"：补标点（字幕体验关键）
  - "hotword"：人名/术语强化
  - 文档明确“带时间戳输出”，可优先走原生 timestamp 生成 srt/vtt

## VAD / “消除幻听”预设（对齐 Whisper-CTranslate2 的“消除幻听版”）

建议做成 API 侧预设档：

- preset="default"：兼顾召回与速度
- preset="anti_hallucination"：更激进的 VAD（更少静音误识别，但可能漏掉弱语音）

实现方式：把 preset 映射到一组 "vad_kwargs" + "merge_vad/merge_length_s" 组合。

## 实现清单（待实施，按验收驱动）

### 1) API 参数扩展（/v1/audio/transcriptions）

- "response_format"：新增/确认支持值
  - 单格式：txt/json/verbose_json/srt/vtt/tsv
  - 多格式：all（默认 zip）
- 字幕/分行参数（只影响渲染，不影响模型推理）
  - "max_line_width"：每行最大字符数（可选）
  - "max_words_per_line"：每行最大词数（可选；中文可按字/词策略定义）
  - "max_line_count"：每段最大行数（可选）
- VAD/降幻听参数
  - "vad_preset"：default / anti_hallucination
  - "merge_vad"：true/false
  - "merge_length_s"：合并后的目标段长度（秒）
  - "vad_kwargs"：仅开放白名单字段（避免任意 kwargs 注入）
- 输出增强参数
  - "language"：语言提示（可选）
  - "use_itn"：仅对 SenseVoice 生效（可选）
  - "hotword"：热词（可选，逗号分隔或多值）

### 2) 渲染器（纯后处理模块）

- 输入统一为：segments[{start,end,text}] + full_text + meta
- 输出格式
  - txt：按分段+断行规则输出（段与段之间用空行或换行，规则需要明确）
  - json：结构化（包含 segments、meta、full_text）
  - srt/vtt：以“句/段级时间戳”为准生成字幕
  - tsv：行级导出（start\tend\ttext）
- all(zip)
  - zip 内文件：output.txt/output.json/output.srt/output.vtt/output.tsv
  - 编码：UTF-8（不加 BOM，避免字幕解析器误判；如你坚持加 BOM，需要单独约定）

## 测试与验收（先规划，后实现）

新增功能必须先写测试（计划如下）：

- 输出渲染器单测（纯 Python，不依赖 GPU/模型）：
  - 输入 segments（含 start/end/text）→ 输出 srt/vtt/txt 的格式正确性
  - 断行规则（max_line_width/max_words_per_line/max_line_count）
  - “all” 协议字段完整性
- API 级单测（不跑真实大模型）：
  - mock 一个最小 "segments" 输入，验证 response_format 分支逻辑

### 验收样例（建议作为测试夹具）

统一输入（mock）：

```json
{
  "full_text": "大家好今天开会讨论项目进度。第二部分讨论风险。",
  "segments": [
    {"start": 0.00, "end": 3.20, "text": "大家好今天开会讨论项目进度。"},
    {"start": 3.20, "end": 6.50, "text": "第二部分讨论风险。"}
  ],
  "meta": {"model": "sensevoice", "device": "cuda", "language": "zh"}
}
```

期望输出（SRT，句/段级）：

```text
1
00:00:00,000 --> 00:00:03,200
大家好今天开会讨论项目进度。

2
00:00:03,200 --> 00:00:06,500
第二部分讨论风险。
```

期望输出（TSV）：

```text
0.00\t3.20\t大家好今天开会讨论项目进度。
3.20\t6.50\t第二部分讨论风险。
```

期望输出（all zip）：

- zip 内必须包含 5 个文件（output.txt/output.json/output.srt/output.vtt/output.tsv）
- 各文件内容与单格式输出一致

## 需要你确认的执行口径（确认后才进入改代码阶段）

1) "all" 采用哪种协议？
   - JSON 内含多格式（推荐）
   - zip 打包
2) 字幕时间戳的最低要求：
   - 只要句级（段级）即可
   - 必须词级（将引入 fa-zh 或其他对齐算法）
3) “消除幻听”优先级：
   - 只做 VAD 预设
   - 同时做额外规则（例如丢弃短句/置信度阈值等）

已确定（本次执行口径）：

- "response_format=all"：zip 打包
- 时间戳：句/段级

## 上游版本与“随时更新”的口径（规划）

### 1) 当前项目是否是上游最新？

- 当前仓库内置的 FunASR 版本号为 "1.3.9"（见 ["app/funasr/version.txt"](file:///y:/NewStore/AI/FunASR-Portable-GPU/app/funasr/version.txt)）。
- 以 "modelscope/FunASR" 的 Tag 为准：上游存在 "v1.3.9"（commit "11b04b8"）。
- 结论：版本号层面，本项目与上游 "v1.3.9" 同代；但当前仓库未固化“上游 commit id”，无法仅凭版本号证明源码与某个 commit 字节级一致。

### 2) 是否能随时更新？

可以更新，但需要明确这是“受控升级”，原因：

- 本仓库对便携包做了本地脚本与运行时封装（BAT/启动器/API 包装），直接覆盖上游代码容易引入不兼容。
- 上游模型/参数/接口经常增加，升级需要跑一遍最小回归（例如 compileall + 冒烟测试）。

建议的升级机制（确认后再实施）：

- 把 "app/"（尤其 "app/funasr"）视为上游镜像：通过 git subtree/submodule 或“定期对比上游 tag/commit”的方式同步。
- 每次同步遵循：拉取上游 → 合入本地定制补丁 → 跑幂等校验（lint/test/type-check/build 或至少 compileall）→ 再发布便携包。

## 版本锁定策略（规划）

目标：既能“随时更新”，也能“可复现”，并且能清晰回答“当前便携包对应上游哪个版本/哪些模型版本”。

### 1) FunASR 代码版本（项目版本）

- 锁定维度：
  - "上游 tag"（例如 "v1.3.9"）
  - "上游 commit id"（例如 "11b04b8..."）
- 推荐落地方式（择一）：
  - 方式 A：在仓库根目录新增一个版本记录文件（例如 "UPSTREAM.lock"），写入：repo、tag、commit、同步日期
  - 方式 B：将 "app/funasr" 变成 submodule/subtree，并在文档中固定引用的 commit
- 升级流程（受控）：
  - 只允许从 "tag" 或明确 "commit" 升级，不直接“追 main HEAD”
  - 每次升级都要跑最小回归（compileall + 冒烟测试脚本）

### 2) 模型版本（ModelScope/HF）

模型并不是一个固定版本号就结束，通常还存在 revision 的概念，因此需要同时锁：

- 锁定维度：
  - model id（例如 "iic/SenseVoiceSmall" / "paraformer-zh" / "ct-punc" / "fsmn-vad" / "fa-zh"）
  - hub（ms/hf）
  - model_revision（若不指定通常为 "master"/最新）
  - 本地缓存路径（本项目固定在 "workspace/models"）
- 推荐落地方式（择一）：
  - 方式 A：在 API 的 "MODEL_CONFIGS" 中为每个模型补 "model_revision" 字段（默认可用 "master"，但允许你切换到固定 revision）
  - 方式 B：在 "workspace/models" 内固化下载产物，并在一个清单文件中记录每个模型对应的 revision 与下载时间（离线可复现）

### 3) 与本次“输出模板/字幕”升级的关系

- 字幕一致性很依赖模型输出稳定性，建议：
  - 生产环境：锁定 FunASR commit + 锁定模型 revision
  - 试验环境：允许更新模型 revision，但每次更新要跑一组字幕样例对比
