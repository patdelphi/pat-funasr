# Pat-FunASR 后端能力整合与精细转录规划

- 文档日期：2026-08-23
- 工作区：`Y:\NewStore\AI\pat-funasr`
- 文档状态：规划已确认，实施中
- 规划边界：忽略 Docker；本轮不修改业务接口、模型代码、数据库和前端 Tab，不联网下载模型，不调用外部 LLM，不执行部署或 Git 写操作
- 正式用途：本文是后续开发依据；`todo.md` 只负责同步获批任务和跟踪进度

## 一、结论先行

1. 当前 8 个前端 Tab 可以在产品层逐步收敛，但不应把所有后端能力强行合成一个巨型接口。
2. 保留 OpenAI-compatible `/v1/audio/transcriptions`，并保留流式、情感、说话人分离、翻译和模型管理等专用端点；它们适合直接调用、独立测试和兼容现有客户端。
3. 把上传、模型目录、模型加载、音频处理、ASR、说话人分离、时间轴、导出、任务状态、LLM 调用和异常处理抽成共享服务，避免不同 Tab 各自实现一次。
4. 新增“工作流编排层”。用户在前端一次提交完整配置，后端按显式配置执行多个阶段；这可以做到“一次操作完成转录和说话人识别”，但内部仍然是可观测、可重试的多阶段流程。
5. 当前精细转录没有正确识别说话人的主要原因不是模型绝对不能完成，而是精细转录只调用普通转写端点，`diarization=True` 没有被该端点接收和执行。
6. 高质量会议转录推荐把“文本识别”和“说话人分离”作为两个可独立选模的分支，再按统一时间轴对齐；不要要求一个 ASR 模型同时承担所有角色。
7. 多模型转录可行。用户必须自己选择主转录模型和一个或多个校对模型；系统只展示能力、资源风险、依赖校验和推荐预设，不静默替用户选模型。
8. LLM 只能做受约束的文本校对、纪要和结构化整理，不能凭语言内容重新发明时间戳、说话人身份或没有音频证据的词句。

## 二、已确认的现状与问题

### 2.1 当前前端与后端映射

| 当前 Tab | 当前主调用 | 当前底层能力 | 是否与其他页面重复 | 规划结论 |
| --- | --- | --- | --- | --- |
| 离线识别 | `/v1/audio/transcriptions`、模型状态/加载端点 | 上传、ASR、VAD、时间戳、导出、批处理 | 与精细转录重复上传、ASR、分段、导出 | 作为“转录工作台”的快速模式 |
| 流式识别 | `/v1/funasr/streaming` | 有状态分片、Mic、Streaming ASR | 与离线链路仅共享模型和错误处理 | 保留独立入口，不与离线接口合并 |
| 说话人分离 | `/v1/funasr/diarization` | ASR + CAM++ + 时间戳 + speaker 分段 | 与精细转录目标重复，但后者未调用 | 作为工作台预设和独立快捷入口，后端只保留一份 diarization 服务 |
| 情感识别 | `/v1/funasr/emotion` | SenseVoice 或 emotion2vec | 可成为工作流可选阶段 | 保留专用端点，并复用到工作流 |
| 跨语言翻译 | `/v1/translations` | NLLB 文本翻译 | 可成为转录后处理阶段 | 保留专用端点，并复用到工作流 |
| 音频工具 | WebUI 本地 `process_audio()` | FFmpeg 降噪、重采样、静音裁剪、响度归一化 | 与精细转录前处理重复 | 抽成共享 media service；独立工具只作为快捷入口 |
| 精细转录 | WebUI 本地 pipeline，再调用 `/v1/audio/transcriptions` 和 LLM | 分块 ASR、LLM 润色、纪要、思维导图、SQLite | 重复并绕过后端已有 diarization、导出和模型能力逻辑 | 迁移为显式后端工作流，前端只配置和展示 |
| 服务与调试 | `/health`、`/v1/models`、状态/加载端点、本地日志 | 模型目录、运行状态、日志 | 模型能力矩阵在前后端重复维护 | 保留入口，模型目录以后端为唯一来源 |

### 2.2 说话人识别没有在精细转录中生效

代码证据如下：

- `app/pat_funasr_webui/fine_transcription/transcription_pipeline.py:187-228` 的 `_call_asr()` 只调用 `/v1/audio/transcriptions`。
- `_call_asr()` 会把场景模板的 `asr_params` 全部作为表单字段发送，其中包含 `diarization=True`。
- `app/openai_api/server.py:1291-1313` 的 `/v1/audio/transcriptions` 没有 `diarization`、`spk_model`、`spk_mode` 或 `preset_spk_num` 参数；额外字段不会触发说话人分离。
- `timestamp_granularities[]` 也不是当前转写端点的声明参数，不能依赖它强制产生字词时间戳。
- 真正执行说话人分离的是 `app/openai_api/server.py:1660-1724`：加载 `spk_model`，调用 `return_spk_res=True`、`output_timestamp=True`，再把 `sentence_info.spk` 归一为 `segments[].speaker`。
- 精细转录前端目前只有 ASR 模型和一个“音频前处理”开关，没有说话人模型、模式、人数、对齐策略或失败降级选项。

因此，当前“会议精细转录”即使场景模板写了 `diarization=True`，结果也通常只有 `speaker=None`。这属于调用链缺失，不应被解释为“模型无法一次完成”。

### 2.3 新发现的精细转录前处理问题

- 独立音频工具正确把 `process_audio` 导入为 `preprocess_audio`。
- 精细转录管线在 `transcription_pipeline.py:321-334` 动态导入不存在的 `audio_processor.preprocess_audio`，并把异常吞掉后使用原始音频。
- 结果是用户勾选前处理后，界面仍可能继续执行，但实际没有降噪、重采样或静音处理，也没有给出可见失败状态。

该问题必须在工作流开发前先修复，并改为“阶段失败可见 + 用户选择停止或降级”，不能继续静默回退。

### 2.4 当前重复实现和边界漂移

| 重复/漂移点 | 当前表现 | 风险 | 目标 |
| --- | --- | --- | --- |
| 模型配置 | API、批处理、预下载脚本、WebUI 静态矩阵分别维护 | 已产生测试失败和 capability 漂移 | 单一模型目录，其他代码只读取和筛选 |
| 模型类型校验 | 转写端点只检查 alias 是否存在；WebUI 静态集合还包含 streaming 模型 | 翻译、情感或 streaming 模型可能进入离线 ASR | 后端按 capability 强校验，前端按服务返回过滤 |
| 上传与临时文件 | 转写、情感、diarization 各自整文件读入；WebUI 多处写 `%TEMP%` | 内存、清理和限制不一致 | 一个上传/临时文件服务，流式写入和 TTL 清理 |
| FFmpeg | `audio_processor.py` 使用硬编码路径；pipeline 直接调用命令名 | 同机不同入口行为不同 | 一个 FFmpeg 路径解析和媒体处理服务 |
| ASR | 普通转写、diarization、精细 pipeline 各自组装参数 | 参数、长音频和异常策略不一致 | 一个 ASR service，端点只做协议适配 |
| 长音频 | 精细 pipeline 自己切 240 秒并重叠 10 秒 | 假流式、原始文本重复、speaker 跨块漂移 | 工作流统一切块、实时事件和全局对齐 |
| 时间轴 | `segmentation.py`、精细分块、diarization 各自处理 | 伪时间戳和 speaker 对齐不可追踪 | 一个 canonical timeline/alignment service |
| 导出 | API renderer、WebUI 转写导出、diarization 导出、精细导出分别组装 | 格式和编码漂移 | 一个 artifact/export service |
| LLM | 精细 pipeline 直接请求兼容接口 | 熔断、协议、Prompt 和密钥边界混杂 | provider adapter + 受约束任务服务 |
| 任务状态 | Gradio generator、本地 SQLite、模型状态各自维护 | 无统一取消、重试和审计 | workflow job + stage event + artifact 清单 |

## 三、接口到底合并到什么程度

### 3.1 保留的外部端点

以下端点保留，不改现有主契约：

- `POST /v1/audio/transcriptions`：OpenAI-compatible 单模型离线转写。
- `POST /v1/funasr/streaming`：有状态低延迟流式识别。
- `POST /v1/funasr/diarization`：直接获得带 speaker 的快速结果。
- `POST /v1/funasr/emotion`：独立情感识别。
- `POST /v1/translations`：独立文本翻译。
- `GET /v1/models`、模型状态/加载端点、`GET /health`：运行和模型管理。

保留原因：协议清晰、容易单测、可被外部客户端直接使用，也便于工作流内部复用。合并端点并不能自动消除重复；消除重复应发生在 service 层。

### 3.2 新增工作流编排端点

建议新增：

| 端点 | 用途 |
| --- | --- |
| `POST /v1/funasr/workflows/validate` | 在上传前校验 workflow、模型能力、依赖和资源风险 |
| `POST /v1/funasr/workflows` | multipart 上传媒体和完整 workflow JSON，返回 `202 + job_id` |
| `GET /v1/funasr/workflows/{job_id}` | 获取任务、阶段、配置快照、结果和错误 |
| `GET /v1/funasr/workflows/{job_id}/events` | SSE 获取阶段进度；无法使用 SSE 时前端轮询任务状态 |
| `POST /v1/funasr/workflows/{job_id}/cancel` | 请求取消未完成阶段 |
| `GET /v1/funasr/workflows/{job_id}/artifacts` | 列出 TXT/JSON/SRT/VTT/TSV/ZIP 等产物 |

“一次完成”的准确含义是：用户只提交一次配置、只创建一个任务；后端内部按阶段执行并持续上报，而不是把所有模型塞进一个不可重试的同步函数。

## 四、目标后端结构

```text
外部兼容端点 ─┐
专用能力端点 ─┼─> 协议适配/校验 ─> 共享服务 ─> 模型运行时
工作流端点   ─┘                  │
                                  ├─ media/upload/temp
                                  ├─ model catalog/manager
                                  ├─ ASR/VAD/chunk
                                  ├─ diarization/alignment
                                  ├─ multi-model reconciliation
                                  ├─ LLM/summary/mindmap/translation/emotion
                                  ├─ task/event/store
                                  └─ render/export
```

建议按小步抽取，不一次性重写 `server.py` 和 `gradio_app.py`：

| 目标模块 | 唯一职责 | 首批调用者 |
| --- | --- | --- |
| `model_catalog` | alias、模型 ID、能力、语言、时间戳、资源等级、约束 | API、WebUI、批处理、预下载 |
| `model_manager` | single-flight 加载、状态、设备队列、并发和卸载边界 | 所有模型端点、workflow |
| `media_service` | 流式上传、大小限制、FFmpeg 探测/转换、临时目录和清理 | 转写、情感、diarization、workflow |
| `asr_service` | 参数白名单、VAD、长音频切块、模型调用、canonical segments | 转写、diarization、workflow |
| `diarization_service` | CAM++、speaker turns、全局 speaker 管理 | diarization、workflow |
| `alignment_service` | 字词/句段/说话人/多模型候选对齐 | workflow、导出 |
| `reconciliation_service` | 多模型一致性、冲突和不确定项 | workflow |
| `llm_service` | provider 适配、熔断、受约束校对和结构化输出 | workflow |
| `artifact_service` | 统一 JSON schema 和多格式导出 | 所有端点和页面 |
| `workflow_service` | 校验 DAG、阶段执行、事件、取消、重试和降级 | 新工作流端点 |

## 五、前端 Tab 合并建议

### 5.1 最终建议的 4 个顶层入口

| 新入口 | 合并来源 | 说明 |
| --- | --- | --- |
| 转录工作台 | 离线识别 + 说话人分离 + 精细转录 | 用预设区分快速转录、会议、访谈、高质量多模型；高级区完整展示所有流程 |
| 实时识别 | 原流式识别 | Mic 和文件流式有状态，保留独立体验 |
| 媒体与文本工具 | 音频工具 + 跨语言翻译 + 情感识别 | 提供独立快捷工具；同样调用共享服务 |
| 模型与服务 | 服务与调试 | 模型目录、加载、资源、日志、任务队列和健康状态 |

实施顺序为：阶段 1 先让 8 个旧入口调用相同共享服务和组件；阶段 2 把旧入口归入新工作台子栏目；阶段 3 在行为和用户路径验证通过后移除重复顶层页面。2026-08-23 已完成三个阶段，最终界面只保留 4 个顶层入口。

### 5.2 转录工作台的信息架构

1. 选择场景预设：快速转录、会议、访谈、课堂、法律、医疗、通用、自定义。
2. 选择输入媒体和输出目标。
3. 展示“流程设计器”：每个可选阶段一张卡片，有启用开关、模型、关键参数、输入输出和预计资源。
4. 展示依赖关系和冲突；不满足时禁止执行并给出可操作原因。
5. 展示运行前配置摘要，用户确认后提交。
6. 运行中按阶段展示模型、进度、耗时、降级、重试和失败原因。
7. 结果页同时保留原始转录、候选差异、speaker 时间轴、校对结果、纪要和下载产物。

### 5.3 转录实时状态中心

现有精细转录使用单个状态文本框反复覆盖消息，warning/error 容易被后续 progress 淹没，也无法判断任务卡在哪个模型或分块。新工作台必须提供持续可见的状态中心，并与后端 workflow event 一一对应。

```text
┌─ 任务状态 ───────────────────────────────────────────────┐
│ 运行中  总进度 46%  已耗时 08:31  当前：主模型转录 3/8   │
│ 模型：qwen3-asr      队列：运行中      [取消任务]        │
├─ 阶段轨迹 ───────────────────────────────────────────────┤
│ ✓ 上传  ✓ 探测  ✓ 前处理  ● 主转录  ○ 校对  ○ Speaker  │
│ ○ 对齐  ○ LLM  ○ 纪要  ○ 导出                           │
├─ 实时事件 ───────────────────────────────────────────────┤
│ 16:40:01 INFO     [upload] 文件接收完成                   │
│ 16:40:05 SUCCESS  [preprocess] 16kHz mono                 │
│ 16:41:22 WARNING  [asr:qwen3-asr] 第 2 块重试 1/2        │
│ 16:41:55 ERROR    [asr:qwen3-asr] ASR_TIMEOUT 可重试      │
│ [全部] [警告] [错误] [自动滚动] [复制] [下载日志]        │
└──────────────────────────────────────────────────────────┘
```

状态中心分三层：

1. 顶部摘要：任务状态、总体进度、当前阶段、当前模型、分块/模型序号、已耗时；只有后端有稳定估算时才显示预计剩余时间。
2. 阶段轨迹：显示 `pending/running/success/warning/error/skipped/cancelled`，历史 warning/error 不因后续成功而消失。
3. 追加式事件日志：按时间记录 info、progress、success、warning、error；支持筛选、自动滚动开关、复制和下载。

用户操作：

- 任务运行中显示“取消任务”。
- 仅当事件包含 `retryable=true` 时显示“重试阶段”或“重试任务”。
- 取消和重试都产生新的事件，不在前端静默修改状态。
- 任务结束后保留完整事件记录，并随 ZIP 导出配置快照和脱敏日志。

### 5.4 状态事件协议

后端是进度和错误的唯一事实来源；前端不得根据定时器虚构百分比或错误原因。

```json
{
  "event_id": 37,
  "job_id": "wf_123",
  "timestamp": "2026-08-23T16:41:55+08:00",
  "level": "error",
  "stage": "transcription.primary",
  "stage_status": "error",
  "progress": 0.46,
  "model": "qwen3-asr",
  "current": 3,
  "total": 8,
  "message": "主模型第 3 个分块转录超时",
  "error_code": "ASR_TIMEOUT",
  "retryable": true,
  "trace_id": "trace_abc",
  "details": {}
}
```

事件约束：

- `progress` 必须单调不减；未知时为 `null`，不填假值。
- `message` 面向用户，`error_code` 面向检索和自动化；内部异常堆栈只写服务端日志。
- 默认脱敏 Token、Header、Prompt、用户名和绝对文件路径。
- 阶段至少覆盖上传、媒体探测、前处理、VAD/切块、模型加载、主模型、各校对模型、diarization、时间轴对齐、共识校对、LLM、纪要、思维导图、翻译、情感、导出和存储。
- SSE 与任务详情接口返回同一事件结构；轮询只是传输方式降级，不维护第二套状态。

## 六、前端必须完整列出的流程选项

场景预设只能填充默认值，不能隐藏或锁死这些选项。

| 阶段 | 前端字段 | 依赖/校验 | 默认原则 |
| --- | --- | --- | --- |
| 输入校验（必经、只读可见） | 文件、格式、时长、声道、采样率、大小 | 文件必须可读；超限在上传时拒绝 | 始终执行并展示结果 |
| 视频取音 | 启用、音轨、输出格式 | 视频输入才显示 | 自动建议，用户确认 |
| 声道策略 | 保留/混合单声道/左右声道分别处理 | 多声道输入才显示 | 会议录音优先保留或显式混合 |
| 降噪 | 启用、强度 | 过强可能损伤语音 | 默认关闭或低强度 |
| 重采样 | 启用、目标采样率、位深 | 目标模型有输入约束 | 推荐 16k/mono，但用户可改 |
| 响度归一化 | 启用、目标响度 | 无 | 默认按预设 |
| 静音处理 | 保留时间轴/裁剪静音、阈值、最小时长 | 裁剪会改变原始时间轴；字幕或 speaker 对齐时必须保存映射 | 高质量会议默认保留时间轴 |
| VAD | 启用、preset、单段最大时长、merge、merge length | 模型需支持；与固定切块共同启用时说明优先级 | 默认启用模型 VAD |
| 长音频切块 | 启用、块长、重叠、并行/串行 | speaker 全局一致性和重叠消重必须开启 | 长音频自动建议，最终用户确认 |
| 转录模式 | 单模型/多模型 | 多模型必须选择主模型和校对模型 | 默认单模型 |
| 主转录模型 | 模型、语言、热词、ITN、PUNC、模型专属参数 | 必须具有 `offline_asr`；不能选 streaming/情感/翻译模型 | 用户必选 |
| 校对模型 | 一个或多个模型、顺序、权重、模型专属参数 | 不能与主模型重复；必须具有 `offline_asr` | 用户必选，不自动追加 |
| 执行策略 | 串行/并行、最多并发数、显存不足处理 | 不能超过后端安全上限；并行需资源检查 | GPU 默认建议串行 |
| 时间戳 | 关闭/段级/字词级、时间戳来源 | SRT/VTT、speaker 对齐和多模型对齐需要时间戳 | 精细转录默认开启 |
| 强制对齐 | 启用、对齐模型、失败回退 | 所选 ASR 缺少稳定时间戳时必选 | 需要时显式建议 |
| 说话人分离 | 启用、策略、ASR 辅助模型、speaker 模型 | 关闭后隐藏 speaker 参数 | 会议/访谈预设开启 |
| speaker 参数 | `spk_model`、`spk_mode`、预设人数、最小/最大人数、重叠语音处理 | 人数必须为正整数或自动；模型能力必须匹配 | 当前先支持 CAM++ 已有参数 |
| speaker 对齐 | 全局/分块后聚类、重叠分配规则、低置信标记 | 依赖时间戳和 diarization | 长会议默认全局 |
| 多模型校对 | 主模型优先/加权共识/仅冲突区校对、差异阈值 | 依赖 canonical timeline | 默认主模型优先、冲突可见 |
| LLM 文本校对 | 启用、provider profile、模型、范围、Prompt、温度/上限 | 不得把 API Key 放入 workflow；与摘要/导图分别开关 | 默认只处理冲突区或明显错词 |
| 标点/ITN/热词后处理 | 启用、策略、热词表 | 不能重复执行破坏模型原生结果 | 按模型能力建议 |
| 情感识别 | 启用、模型、粒度 | 模型需具有 emotion 能力 | 默认关闭 |
| 翻译 | 启用、模型、源语言、目标语言 | 转录完成后执行 | 默认关闭 |
| 纪要 | 启用、LLM 模型、模板、结构 schema | 即使关闭 LLM 文本校对，也可独立启用 | 会议预设开启 |
| 思维导图 | 启用、LLM 模型、模板、结构 schema | 必须使用安全渲染和本地静态资源 | 默认关闭，用户选择 |
| 存储 | 不保存/保存任务、保留天数、是否保存原音频 | 涉及数据库和隐私策略 | 默认最小保存 |
| 导出 | JSON/TXT/SRT/VTT/TSV/ZIP、原始/校对/对比版本、speaker 名称 | 字幕格式依赖时间戳 | 用户多选 |

### 6.1 必须实现的前端联动

- 关闭时间戳后，禁用 speaker 对齐、多模型时间轴校对和 SRT/VTT。
- 选择多模型后，要求至少一个主模型和一个校对模型；重复模型报错。
- 选择不支持 offline ASR 的模型时，前端不允许提交，后端也必须再次拒绝。
- 选择“裁剪静音”且还要原始音频字幕时，要求启用时间映射；未实现映射前直接禁止该组合。
- 关闭说话人分离后，隐藏 speaker 模型、模式和人数，但保留用户先前填写值以便恢复。
- 选择分块 diarization 时，必须同时选择跨块 speaker 聚类；否则提示 speaker 编号可能在每块重置。
- 纪要、思维导图、文本校对各自选择 LLM 模型，不能因为关闭“文本校对”就误判其他 LLM 阶段不需要模型。
- 未下载模型只能显示为“未下载”，不能在点击执行时静默联网下载；下载或模型来源切换需用户单独确认。
- 资源不满足时后端可拒绝或按用户预先选择的策略降级，不能私自更换模型。

## 七、模型选择器与模型目录

### 7.1 `/v1/models` 作为唯一模型事实源

现有 `/v1/models` 已返回 `ready`、`downloaded` 和基础 capabilities，建议兼容性扩展以下字段：

```json
{
  "id": "qwen3-asr",
  "label": "Qwen3-ASR-1.7B",
  "kind": "asr",
  "capabilities": {
    "offline_asr": true,
    "streaming_asr": false,
    "timestamps": ["segment", "word_with_aligner"],
    "diarization": false,
    "hotword": "model_dependent",
    "languages": ["zh", "en"]
  },
  "runtime": {
    "downloaded": true,
    "state": "ready",
    "device": "cuda",
    "dtype": "fp16"
  },
  "resource_profile": {
    "vram_risk": "high",
    "speed_grade": "slow",
    "source": "local_measurement_or_estimate"
  },
  "constraints": []
}
```

示例只表达 schema，不代表已经实测该模型的显存和速度。资源数值必须标注来源；没有实测时只显示低/中/高风险，不能伪装成精确 MB 或实时倍速。

### 7.2 多模型选择组件

- “主模型”是单选；“校对模型”是可排序多选列表。
- 每个校对模型显示角色、权重、语言、时间戳来源、显存/速度风险、下载和加载状态。
- 用户可以拖动顺序；顺序用于串行模式和无多数共识时的优先级。
- 权重用于候选冲突，默认值由预设填入，但必须可编辑。
- 前端展示预计需要加载的模型集合，执行前明确说明是否会切换/释放模型。
- 后端只做能力校验、资源上限和安全限制，不自动增加校对模型。

### 7.3 “模型与服务”页面重组

现有页面把模型能力、服务状态、推荐入口、原始 JSON、日志刷新和调试信息混在同一层级。目标页面拆成五个职责明确的区域：

| 区域 | 默认可见信息 | 主要操作 | 数据来源 |
| --- | --- | --- | --- |
| 服务概览 | API 可用性、设备、已加载模型数、运行/等待任务数、最近错误 | 刷新、进入诊断 | `/health` + workflow queue 摘要 |
| 模型管理 | 模型名称、类型、能力、语言、下载/加载状态、设备、资源风险 | 筛选、显式加载；下载/切源/卸载需独立确认和能力支持 | 扩展后的 `/v1/models` 和模型状态端点 |
| 运行资源 | CPU、RAM、GPU、显存、模型占用、并发上限 | 刷新、查看占用来源 | 新增只读 runtime status；不可用字段显示“不可用” |
| 任务队列 | 运行中、等待中、完成、失败任务及阶段/模型/耗时 | 查看详情、取消、按能力重试 | workflow job/event |
| 诊断与日志 | 原始 health/models JSON、服务日志、trace 查询、高级参数 | 复制、下载脱敏日志、手动刷新 | 现有调试接口和本地日志读取 |

布局原则：

- 日常操作只需要前四区；“诊断与日志”默认折叠。
- 模型能力和状态只来自后端 model catalog，删除 WebUI 重复静态矩阵的决策职责；前端本地文字只做展示翻译。
- 下载模型、切换 hub、卸载模型和改变设备属于高影响操作，不放在普通“刷新”路径中，也不随执行任务静默发生。
- 运行资源没有可靠采样时显示“不可用”；不得把文档估算当实时显存。
- 任务队列直接复用状态事件，点击任务展开与转录工作台相同的状态中心。
- 原始 JSON 和日志不再占据首页主视图，避免“服务正常但页面看起来全是调试输出”。

## 八、工作流配置草案

以下是“会议高质量、多模型、独立说话人分离”的完整配置示例。所有模型均是用户选择或预设填入后由用户确认。

```json
{
  "workflow_version": "1.0",
  "preset_id": "meeting_high_quality",
  "preprocess": {
    "enabled": true,
    "noise_reduction": true,
    "noise_strength": 8,
    "sample_rate": 16000,
    "loudnorm": true,
    "silence_mode": "preserve_timeline"
  },
  "segmentation": {
    "vad_enabled": true,
    "vad_preset": "default",
    "chunk_enabled": true,
    "chunk_seconds": 240,
    "overlap_seconds": 10
  },
  "transcription": {
    "mode": "multi_model",
    "primary": {
      "model": "qwen3-asr",
      "weight": 1.0,
      "language": "auto"
    },
    "reviewers": [
      {
        "model": "paraformer",
        "weight": 0.8,
        "language": "zh"
      }
    ],
    "execution": "serial",
    "max_concurrency": 1,
    "resource_failure_policy": "stop_and_ask"
  },
  "timestamps": {
    "level": "word",
    "forced_alignment": true,
    "aligner_model": "qwen3-forced-aligner"
  },
  "diarization": {
    "enabled": true,
    "strategy": "separate_align",
    "asr_model": "paraformer",
    "speaker_model": "cam++",
    "spk_mode": "punc_segment",
    "preset_speaker_count": 3,
    "global_speaker_clustering": true
  },
  "reconciliation": {
    "mode": "primary_first",
    "disagreement_threshold": 0.2,
    "keep_alternatives": true,
    "uncertain_policy": "flag_for_review"
  },
  "llm_proofread": {
    "enabled": true,
    "provider_profile_id": "local-openai-compatible",
    "model": "user-selected-model",
    "scope": "disagreements_only",
    "preserve_timestamps": true,
    "preserve_speakers": true
  },
  "summary": {
    "enabled": true,
    "provider_profile_id": "local-openai-compatible",
    "model": "user-selected-model",
    "template_id": "meeting"
  },
  "mindmap": {
    "enabled": false
  },
  "translation": {
    "enabled": false
  },
  "emotion": {
    "enabled": false
  },
  "export": {
    "formats": ["json", "txt", "srt", "vtt", "tsv", "all"],
    "include_raw_candidates": true,
    "include_config_snapshot": true
  }
}
```

运行前摘要必须把以上配置转为人可读文本，至少显示：阶段顺序、每阶段模型、串并行、预估资源风险、未下载模型、时间戳来源、speaker 策略、失败策略和导出内容。

## 九、说话人识别方案分析

### 9.1 能否一次完成转录和说话人识别

可以分两个层次回答：

- 一次前端操作/一次工作流提交：可以，而且应该这样设计。
- 一次模型推理：当前 `/v1/funasr/diarization` 对支持的模型可以在一次 `generate()` 中请求 ASR、时间戳和 speaker 结果；但内部模型链仍包含 ASR/VAD、speaker embedding、聚类和分段。
- 高质量多模型会议：不建议强求一次模型推理。推荐 ASR 分支与全局 diarization 分支独立运行，再在 canonical timeline 上对齐，准确率、可替换性和失败隔离更好。

### 9.2 推荐的高质量会议链路

1. 在原始时间轴上探测媒体、声道和时长。
2. 可选降噪/响度处理，但默认不删除静音；如删除必须保存原始时间映射。
3. 生成 VAD speech regions 和长音频 chunk 计划。
4. 主 ASR 和用户选择的校对 ASR 产生带时间戳候选。
5. CAM++ 在尽量完整的会议范围生成 speaker turns；长音频时必须跨 chunk 统一 embedding 聚类，避免每块都从 `spk0` 重新编号。
6. 把 ASR words/segments 与 speaker turns 按时间重叠对齐；一个句段跨 speaker 时拆段。
7. 对重叠语音、边界不确定或无 speaker 的段落标记 confidence/uncertain，不强行填充。
8. 多模型只校对文字；speaker ID 仍来自音频 diarization。
9. 用户可把 `spk0`、`spk1` 重命名为真实姓名；模型不得未经用户确认推断身份。
10. 导出保留机器 speaker ID、用户显示名、原始和校对文本、时间戳及工作流快照。

### 9.3 为什么长会议不能逐块独立分离后直接拼接

- 同一说话人在不同块中可能被分配不同 speaker 编号。
- 两个音色相近的人可能在某一小块中被合并，在另一块中被拆分。
- 10 秒重叠区会重复产生文本和 speaker turns。
- 当前精细 pipeline 只对 segments 做有限去重，`raw_text` 仍直接拼接；speaker 全局一致性尚未实现。

因此，若做分块 diarization，必须保存 speaker embedding 并在全局二次聚类，或采用全局 diarization 后再切分结果。

### 9.4 speaker 对齐规则

- word 与 speaker turn 重叠比例高于阈值时归属该 speaker。
- 一个 segment 内出现 speaker 切换时，按 word 时间戳拆成多个 segment。
- 无 word 时间戳时只能做句段级近似对齐，并在结果中标注 `alignment_quality=approximate`。
- 重叠语音允许 `speakers=[spk0, spk1]` 或 `overlap=true`，不要随意只选一个。
- speaker 边界不确定时保留 `speaker=null` 和候选列表，而不是由 LLM猜测。

## 十、多模型转录与校对方案

### 10.1 模型角色分开

| 角色 | 用户选择 | 职责 |
| --- | --- | --- |
| 主转录模型 | 必选 1 个 | 产生主文本和主时间轴 |
| 校对 ASR 模型 | 可选 1 个或多个 | 独立听音频，提供候选文本 |
| 强制对齐模型 | 条件必选 | 在 ASR 缺乏稳定字词时间戳时提供锚点 |
| speaker 模型 | 说话人分离启用时选择 | 产生 speaker embedding/turns |
| LLM 校对模型 | LLM 校对启用时选择 | 仅处理有证据的文字冲突 |
| 摘要/导图模型 | 对应阶段启用时选择 | 生成结构化衍生产物 |

主转录模型可以是高精度模型，而 diarization 分支使用支持 CAM++ 的模型；这比为了 speaker 标签而强制降低主文本模型更合理。

### 10.2 对齐和共识流程

1. 所有模型结果先转为统一结构：`text/start/end/words/model/confidence/source`。
2. 选择主模型或强制对齐器作为时间轴锚点。
3. 中文按字/词、拉丁语言按词进行标准化；忽略标点和空白但保留数字、单位和专名。
4. 先按时间重叠匹配，再用编辑距离处理边界抖动；禁止只把整段字符串直接拼接比较。
5. 完全一致的块直接接受；存在差异的块进入 reconciliation。
6. 两模型无多数时默认保留主模型，同时展示校对候选；只有满足用户设定规则时才替换。
7. 三个及以上模型可做加权投票；没有真实模型 confidence 时不能伪造置信度，只能使用用户权重和一致性。
8. 所有自动替换记录 `before/after/evidence_models/rule`，允许用户回看。

### 10.3 LLM 校对边界

LLM 输入应包括：主候选、校对候选、对应时间范围、热词、前后文和允许操作规则。输出必须是结构化 patch，而不是一段不可审计的新全文。

允许：

- 从已有候选中选择更合理的词。
- 依据热词表修正有音近证据的专名。
- 补充标点、大小写和有限 ITN。
- 对冲突给出 `uncertain`，保留多个候选。

禁止：

- 改写事实、数字、姓名、药量或法律条款而没有候选证据。
- 改动时间戳和 speaker ID。
- 根据说话内容猜测真实身份并覆盖机器 speaker。
- 删除口头重复、停顿或语气词，除非用户明确选择“整理稿”而非“逐字稿”。

## 十一、错误处理、资源和安全策略

- 每个 API 保留明确异常映射：输入/能力冲突为 4xx，模型和内部故障为 5xx；不能把已有 `HTTPException(400)` 再包装成 500。
- 上传统一流式写入并累计限制；转写、情感、diarization 和 workflow 使用同一策略。
- 模型首次加载使用 single-flight；同一模型同一配置只能加载一次。
- GPU 推理默认并发上限为 1，用户可选择串行或并行，但后端有不可突破的安全上限。
- 不允许工作流通过任意 `device/hub` 组合无限创建模型缓存变体；模型运行配置使用白名单和容量上限。
- 临时媒体和导出物使用任务目录、引用计数和 TTL 清理；禁止永久散落在 `%TEMP%`。
- LLM provider 使用配置档案 ID，API Key 只在服务端读取；不同协议使用独立 adapter。
- 思维导图不使用不受控 `innerHTML`、不把 LLM 字符串拼入脚本、恢复 iframe sandbox，并把静态资源本地化。
- 远程访问场景必须有鉴权、限流、TLS 和最小权限；本地默认仍绑定 loopback。

## 十二、非 Docker 修正与实施顺序

### 阶段 A：先恢复正确性和测试基线

1. 统一模型配置源，修复当前模型配置回归测试。
2. 修复精细转录前处理函数导入，失败时不再静默。
3. 明确普通转写、流式、情感、翻译、diarization 的 capability 校验。
4. 修复转写参数错误被包装为 500。
5. 为精细 pipeline 增加直接单测，覆盖场景参数、前处理、diarization 开关和 LLM 阶段依赖。

### 阶段 B：抽取共享基础服务

1. 流式上传、大小限制、媒体临时目录和 TTL。
2. FFmpeg/ffprobe 统一路径解析和启动检查。
3. model catalog、single-flight model manager、GPU 任务队列和缓存上限。
4. ASR 统一参数 schema、真实 chunk event、重叠去重和 canonical segment。
5. 统一 renderer/exporter；保留兼容端点现有输出。

### 阶段 C：工作流编排与前端全量选型

1. 先写 workflow schema、依赖校验和配置快照测试。
2. 实现 validate、submit、status、events、cancel、artifacts。
3. 在现有精细转录 Tab 接入新工作流，先不改变其他 Tab。
4. 实现所有阶段卡片、模型角色选择、依赖联动和运行前摘要。
5. 旧离线/diarization/音频工具仍可用，但底层调用共享服务。

### 阶段 D：说话人识别质量链路

1. 复用现有 CAM++ endpoint 的可用能力并抽成 diarization service。
2. 实现全局 speaker turns、跨块 speaker 聚类和 speaker/word 对齐。
3. 输出 alignment quality、重叠语音和不确定段。
4. 增加 speaker 重命名与导出映射，保留原始 speaker ID。

### 阶段 E：多模型校对

1. 实现主模型和校对模型独立运行记录。
2. 实现 canonical timeline、多模型对齐和冲突块。
3. 实现主模型优先和加权共识；先不引入 LLM。
4. 再增加受约束 LLM patch 校对和审计记录。
5. 实现显存不足时按用户预选策略停止、串行或跳过，并在 UI 明确展示。

### 阶段 F：其余审查问题与升级准备

1. 修复长音频假流式和重复文本、LLM 熔断计时、文件句柄和临时文件生命周期。
2. 修复思维导图脚本注入和 CDN 依赖。
3. 明确 OpenAI-compatible 与 Anthropic provider adapter，移除不可用示例或实现真实协议。
4. 清理旧入口、断链、版本文档、绝对路径和文本格式。
5. 增加 CI、PR 模板、compileall、pytest、格式、链接、schema 和安全检查。
6. 重建可复现运行时后，再单独审批依赖联网核验和逐项升级。

## 十三、测试与质量验收

### 13.1 自动化测试

- model catalog 是唯一来源；API、批处理和预下载 alias/config 一致。
- 每个端点拒绝不具备对应 capability 的模型。
- workflow schema 覆盖所有前端字段、依赖、冲突和安全上限。
- 上传超限在内存稳定的情况下返回 413。
- 两个并发首请求只加载一次模型；健康检查不被长推理完全阻塞。
- 长音频每个 chunk 完成立即产生事件，最终 raw text 和 segments 无重叠重复。
- 精细前处理启用时确实调用共享 media service；失败按用户策略处理。
- diarization 开关确实产生 speaker 服务调用；关闭时不调用。
- 分块后同一 speaker ID 跨块保持稳定；无法确定时输出 `null/uncertain`。
- 多模型对齐覆盖中英文、无标点、数字、热词、时间戳漂移和候选缺失。
- LLM patch 不能修改时间戳和 speaker，不能输出未声明字段。
- renderer 对 speaker、原始/校对版本、BOM/CRLF 和 ZIP 内容保持一致。
- 思维导图恶意 HTML/script 只能作为文本展示。

### 13.2 真实音频质量集

建立不小于 12 段、可人工标注的本地验收集，至少覆盖：

- 2 人、3 人、4 人以上会议。
- 近讲、远场、噪声、回声、音量差异。
- 相似音色、快速轮换、打断和重叠语音。
- 中文、英文、中英混合、方言或口音。
- 短音频和超过 30 分钟的长会议。
- 已知与未知 speaker 人数。

质量指标：ASR 使用 CER/WER；diarization 使用 DER/JER；端到端 speaker 文本使用 SA-WER/cpWER；另记录时间戳边界误差、跨块 speaker 一致率和需要人工复核的冲突比例。先生成当前基线，再为每个场景设发布门槛，不能只凭几个样例主观判断“识别好了”。

### 13.3 前端验收

- 所有可选阶段都在前端可见；预设应用后仍可逐项修改。
- 主模型、校对模型、speaker 模型、aligner、LLM、情感和翻译模型均由用户选择。
- 模型卡显示能力、语言、时间戳/speaker 支持、下载/加载状态和资源风险来源。
- 执行前摘要与实际后端配置快照逐字段一致。
- 运行中可看到当前阶段、模型、进度、耗时、重试、降级和失败原因。
- 资源不足时不静默替换模型；只执行用户预先选择的失败策略。
- 输出可回溯到模型候选、自动替换规则、speaker 来源和工作流版本。
- 状态中心持续显示总体进度、当前阶段/模型和追加式事件；warning/error 不被后续 progress 覆盖。
- 状态窗口与 job event 逐字段一致，任务结束后可以筛选、复制和下载脱敏日志。
- “模型与服务”五个区域职责清楚，模型、资源、任务和诊断信息不再混排。
- 模型状态、任务状态和资源状态各有唯一后端事实来源，不在前端维护冲突副本。

## 十四、数据库和迁移边界

当前 SQLite 只记录一个 `asr_model`、segments 和 LLM outputs，无法完整表达多模型候选、workflow 配置、stage event、speaker 映射和 reconciliation patch。实施时可能需要新增任务配置、模型运行、候选、事件和产物表。

数据库 schema 变更属于迁移操作，必须另行给出备份、迁移和回滚方案并再次确认；在确认前先使用内存/文件型任务快照完成 schema 和 API 验证，不直接修改现有数据库。

### 2026-08-23 实施结果

本轮已按非 Docker 范围完成首版可运行实现：

- 建立统一 `model_catalog`，API、批处理和模型预取脚本复用同一模型配置与能力矩阵。
- 建立共享上传限制、模型 single-flight、工作流 schema/job/event、说话人时间对齐、多模型共识和统一产物服务。
- 新增 workflow validate/submit/status/events/cancel/artifacts、runtime status 和任务队列摘要接口。
- 精细转录前端已列出前处理、VAD/分块、主模型、多个校对模型、串并行与失败策略、时间戳/强制对齐、说话人模型、多模型规则、三个独立 LLM 阶段、翻译、情感和导出选项。
- 精细转录执行入口已改接异步工作流；状态窗口持续显示任务、阶段、模型、总体进度、warning/error/error_code，并支持取消和日志随产物导出。
- “模型与服务”已按服务总览、模型管理与能力、运行资源、工作流任务队列、诊断与日志五区重组。
- 说话人识别采用独立全局 speaker 时间轴，再按最大时间重叠映射到主模型时间轴；无证据或近似并列时输出 `speaker=null`、候选和不确定标记，不按文本猜测。
- 多模型校对保留主模型时间轴，以用户权重和模型文本一致性选择候选；结果保留来源、替代项、规则和不确定标记，LLM 后处理不改写时间戳或 speaker。
- 修复长音频重叠去重与 raw text 重复、LLM 熔断起算时刻、思维导图脚本注入/CDN、同步推理阻塞异步路由和工作流临时目录 TTL。
- 增加非 Docker CI、PR 检查清单和 pytest 收集边界；本地全量结果为 `214 passed, 3 subtests passed`，compileall 通过。
- 使用仓库 65 分钟 AAC 的 60 秒片段完成真实工作流冒烟：SenseVoice 主模型、Paraformer 校对、加权共识、Paraformer+CAM++ speaker 时间轴、时间重叠对齐、17 条状态事件和 JSON/TXT/config/events 四个产物全部完成；该片段为单说话人，输出 speaker 0。
- 真实冒烟发现便携运行时缺少 sklearn 时 `ClusterBackend` 未定义；现已增加 NumPy 余弦相似度和 K-Means 后备实现，并增加回归测试。
- 模型加载优先把主模型、VAD、标点和 speaker 模型解析到本地 ModelScope 缓存，关闭版本检查；修复后日志只出现 `Using local ...`，没有下载记录。

数据库仍使用进程内工作流任务状态，没有修改 SQLite schema，符合迁移需再次确认的边界。

### 2026-08-23 二次复核修复结果

- 多模型校对改为聚合同一主时间段内全部重叠 reviewer 子段，并按用户阈值形成近似文本共识；`keep_primary`、候选保留和并行模型顺序均已实际生效。
- 少于 20 个 speaker embedding 时，用户指定的说话人数改用确定性 NumPy K-Means 执行，不再固定返回单 speaker。
- 工作流仅展示并接受已实现的 `separate_align + 全局聚类`；字词级时间戳必须配合支持模型的强制对齐，关闭时间戳时不输出合成时间轴。
- LLM 校对范围和模板已在前端显式可选；逐段校对直接更新带时间戳/speaker 的 segments，纪要和思维导图可选择原始文本或校对后文本，TXT/JSON 与页面最终文本保持一致。
- 服务模型加载改为本地缓存强制模式：模型或 VAD/标点/speaker/aligner 依赖缺失时明确报错，不静默联网；模型目录中的 requirements 默认不自动安装。
- 公开任务快照移除后端绝对路径；WebUI 统一通过 artifact API 下载文件，支持远程 API 部署；异常事件对路径、Token、Authorization 等信息脱敏。
- 音字联动内联 JSON 对 `<`、`>`、`&`、`</script>` 和 Unicode 行分隔符安全转义。
- 事件轮询改为增量读取，任务列表不复制完整历史；终态任务增加 TTL 和数量清理。状态面板支持暂停自动滚动、异常级别筛选、复制和下载。
- `events.jsonl` 在任务终态重写，现与状态事件同为 18 条并以“任务完成”结束；Paraformer 校对和 diarization 复用同一次带 speaker 推理，避免重复加载。
- CI 补齐 scipy/torch；全量回归更新为 `237 passed, 3 subtests passed`，compileall 通过。
- 真实 60 秒离线复测完成：8 个主段全部取得 reviewer 聚合候选，识别出 speaker 0/2/3，Paraformer speaker 结果已复用，4 个产物完整生成，全程没有模型下载或依赖安装。

### 2026-08-23 前端导航收敛补完

- 顶层导航已从 8 个收敛为 4 个：转录工作台、实时识别、媒体与文本工具、模型与服务。
- 转录工作台只保留三个子栏目：快速转录、会议精细转录、说话人时间轴；原离线、精细和说话人能力继续复用原组件、事件和后端接口，没有复制业务实现。
- 媒体与文本工具只保留音频处理、跨语言翻译、情感识别三个子栏目；实时识别保持文件流式与 Mic 两条路径。
- 嵌套父 Tab 改为纯前端导航，不再绑定会与子 Tab 选择事件互相等待的后端回调；服务自动刷新状态由具体功能子页维护。
- 音频处理回调不再把同一组件注册为重复输出；上传组件限定为文件上传，结果播放器为只读，避免首次进入工具页触发不必要的麦克风权限流程。
- 导航结构、子栏目归属、预渲染、音频来源和依赖输出唯一性已加入自动化测试；全量结果更新为 `238 passed, 3 subtests passed`，compileall 通过。
- 重启 CUDA 托管服务后完成浏览器逐项验收：4 个顶层栏目、6 个合并子栏目、实时文件/Mic、会议精细转录状态窗口和模型与服务五区均可正常切换和显示。

### 2026-08-23 实时识别切换崩溃修复

- 用户原页签已从“切换卡住”发展为浏览器 `This page crashed`，同时 API/UI 健康检查仍正常，问题位于浏览器组件挂载而非后端服务失联。
- 第一项放大因素是导航收敛后仍保留临时的 `render_children=True`：10 个顶层及子级栏目会同时预渲染，精细转录、媒体工具、模型服务和实时 Mic 等大型组件共同占用前端资源；现已统一恢复为按需渲染。
- 用户第二次复测证明按需渲染并非完整根因。运行中 `/config` 进一步显示“实时识别”仍绑定后端 `select` 事件，配置为 `queue=true`、`show_progress=full`；该事件只用于关闭服务页自动刷新，却使普通导航进入 Gradio 任务队列，可能被长任务或 SSE 连接阻塞。
- “实时识别”现改为纯前端导航，不再提交后端选择事件；运行中配置确认其 `select` 依赖为 0。流式文件和 Mic 的实际识别事件、参数与接口协议均未修改。
- 两项回归测试均先验证旧实现失败，再约束 10 个栏目 `render_children=false` 且实时 Tab 不得存在 `select` 依赖；相关测试 `47 passed`、全量测试 `238 passed, 3 subtests passed`、compileall 通过。
- 独立干净页面实际切换虽然通过，但测试浏览器无麦克风而用户环境有真实设备，进一步暴露实时页首次挂载仍同时初始化 Mic，以及隐藏音频预览默认带 microphone 来源、全局 `getUserMedia` 覆盖和全页 `MutationObserver` 的风险。
- 实时识别现拆为“文件流式识别 / Mic 实时识别”两个按需子页：默认进入文件页时不创建麦克风组件，只有用户显式选择 Mic 子页才初始化音频设备。隐藏文件音频预览限定为只读 upload 来源。
- 删除全局麦克风设备桥接，不再覆盖浏览器 `getUserMedia`、监听页面全部 DOM 变化或在页面加载时枚举设备。用户环境仍发生 renderer 崩溃后，Mic 子页进一步移除浏览器 `gr.Audio(microphone)`，改用项目已有 PyAudio 系统采集链路。
- 系统 Mic 页面显式列出输入设备，并提供刷新、开始/停止、采集信号、状态和转录输出；后台继续复用原 PCM16/16k 转换与 streaming 接口，控制和轮询回调均不进入 Gradio 队列。
- 本机 PyAudio 枚举出 18 个输入设备；显式选择 `2 - 麦克风 (ToDesk Virtual Audio)` 后，后台采集成功发送 2 个分片，前端实录成功将分片数从 2 更新到 8 并正常停止。实录完成后顶层栏目连续切换 10 轮，页面保持存活。
- 最新相关测试为 `47 passed`，全量测试 `238 passed, 3 subtests passed`，compileall 通过；浏览器不再请求麦克风权限或创建 WebAudio 录音组件。

### 2026-08-23 模型与服务两层菜单

- 顶层“模型与服务”保持不变，原五个 Accordion 改为与其他产品域一致的二级 Tab：服务总览、模型管理、运行资源、任务队列、诊断与日志。
- 五个子页继续使用原组件、状态和后端函数；“检查服务”复用聚合刷新函数，资源和任务子页增加显式刷新按钮，没有复制接口或运行数据来源。
- 父级“模型与服务”改为纯前端导航，不再绑定两次后端 `select` 刷新；子页只使用 `queue=False` 的轻量状态事件，避免再次让菜单切换进入任务队列。
- 导航测试约束五个子 Tab 的名称、顺序、按需渲染和父级无选择依赖。浏览器实际验证五页控件全部可见，“检查服务”“刷新运行资源”“刷新任务队列”均返回真实内容。
- 相关测试 `47 passed`、全量测试 `238 passed, 3 subtests passed`、compileall 通过。

### 2026-08-24 实时识别切换卡死再修复

- 复核发现非服务业务子 Tab 仍残留 6 个后端 `select` 回调；这些回调只用于切换时更新服务自动刷新状态，却会把普通栏目导航提交到 Gradio 请求队列，存在切换阻塞风险。
- 删除快速转录、会议精细转录、说话人时间轴、音频处理、跨语言翻译和情感识别的后端 `select` 事件；业务栏目现在只执行前端 Tab 切换。模型与服务子页保留 `queue=False` 的轻量状态事件，用于启用服务页自动刷新，不承载业务任务。
- 导航回归测试新增所有非服务顶层/子 Tab 不得存在 `select` 依赖的断言；先在旧实现上失败，再在修复后通过。
- 重启 CUDA 托管 API/UI 后，独立页面完成“实时识别→转录工作台/媒体与文本工具/模型与服务”逐项切换；每次点击约 0.3 秒，页面保持存活，浏览器错误/警告为 0。
- 当前验证：全量 `unittest` 为 `234 tests OK`，compileall 通过；API `/health` 与 WebUI 首页均返回 200。

## 十五、完成定义

本规划进入“开发完成”必须同时满足：

1. 兼容端点行为和测试不回退。
2. 所有页面使用共享 model catalog、media、ASR、diarization 和 artifact 服务，不再复制核心实现。
3. 精细转录的说话人分离真实执行，并有可验证的 speaker 时间轴。
4. 用户能在前端选择所有可选阶段和每个模型角色，运行配置可审计。
5. 多模型校对不破坏时间戳和 speaker，所有替换可追踪和撤销。
6. 全量测试为 0 失败、0 非预期警告，真实音频基线和质量门槛有记录。
7. 文档、API schema、UI 字段和实际行为一致。

## 十六、本轮未执行事项

- 未删除或修改 Docker 相关文件。
- 未主动联网核对上游最新版本、安全公告或执行依赖升级；CI 依赖文件按当前本地版本固化。首次真实模型加载暴露旧逻辑会访问 ModelScope 并刷新已缓存模型的 3 个 README 元数据文件；随后已修复为本地缓存优先，复测无下载日志。
- 未下载模型权重、安装依赖或调用外部 ASR/LLM 服务。
- 未执行数据库迁移、部署、push、pull 或 merge；已按用户要求先提交首版基线 `f53acd2`，二次复核修复仍保留在工作区等待确认。
- 仓库内有一段真实 AAC，但没有人工标注集。已完成 60 秒单说话人真实链路冒烟；CER/WER、DER/JER、SA-WER/cpWER、跨块 speaker 一致率及多说话人质量门槛仍需标注集，这属于发布前模型质量验收，不影响本轮代码、契约和真实执行链路结论。
