#
Pat WebUI 开发 Todo

说明：本文件是当前唯一执行清单，只跟踪下一阶段的 "Pat WebUI" 开发工作；已完成的旧事项仅保留归档摘要，不再作为当前待办继续维护。

## 官方参考

- 官方教程："https://modelscope.github.io/FunASR/zh/tutorial.html"
- 官方 API："https://modelscope.github.io/FunASR/api.html"
- 本 Todo 已按上述两份官方文档复核：当前规划覆盖了离线识别、流式识别、说话人分离、情感识别、VAD、标点恢复等主场景

## 复核结论

- 当前第一优先级仍然是做出独立、可运行、不影响旧版的 "Pat WebUI"
- 阶段 A / B 先尽量复用现有 OpenAI-Compatible API，避免一开始就把后端链路一起推翻
- 阶段 C 再评估是否要直连 FunASR 推理链路，以覆盖流式识别、说话人分离、情感识别、VAD、PUNC 等官方完整能力
- Paraformer / Paraformer-Streaming 需要单独考虑 `punc_model="ct-punc"`；Fun-ASR-Nano / SenseVoice / Qwen3-ASR 原生带标点
- 流式识别必须考虑 `cache`、`is_final`、`chunk_size`、`encoder_chunk_look_back`、`decoder_chunk_look_back`
- 说话人分离必须考虑 `spk_model="cam++"`；若未来对接 Qwen3-ASR 说话人分离，还需评估 `forced_aligner`
- VAD 相关参数至少要覆盖 `vad_kwargs.max_single_segment_time`
- 按你的开发规则，新功能要先补最小化测试，再做实现

## 当前口径

- 原 WebUI 保持不动：保留 "app/openai_api/gradio_app.py" 源码，不再保留旧 UI 启动脚本
- 新 WebUI 单独开发：在 "app/pat_funasr_webui/" 下演进
- 当前主目标：先做可运行、可下载、可扩展的 Pat WebUI，再逐步对齐官方完整能力
- 阶段 A / B 默认继续走现有 "/v1/models" 与 "/v1/audio/transcriptions"
- 阶段 C 如需支持官方完整能力，再决定“扩后端 API”或“WebUI 直连 FunASR”
- 进度跟踪方式：只使用标准 checkbox；`[ ]` 未开始，`[x]` 已完成；进行中通过单独写“当前进行中”跟踪
- 当前不再以 "Docs/tasks.md" 作为执行入口

## 当前进行中

- [ ] 阶段 A：Pat WebUI 分支隔离
- [ ] 阶段 B：Pat WebUI 第一版

## 当前专项计划：Pat WebUI 布局与样式统一

说明：本专项只做 `"app/pat_funasr_webui/"` 下的前端收口，不碰原 `"app/openai_api/gradio_app.py"`；以下清单已按“已完成 / 待确认 / 待执行”整理。

### 本次修改范围

- [ ] 仅修改 `"app/pat_funasr_webui/gradio_app.py"` 的布局、按钮样式、文案一致性与少量前端状态展示
- [ ] 如有必要，补充 `"app/pat_funasr_webui/app_utils.py"` 中与模型下拉兜底相关的辅助函数
- [ ] 仅补充最小必要测试：`"tests/test_pat_webui_diarization_exports.py"`、`"tests/test_pat_webui_utils.py"`
- [ ] 不改后端接口协议，不新增部署脚本，不动数据库，不执行危险命令

### 已完成

- [x] P1. 离线识别页主结构已收口
- [x] 顶部控制区已固定为标准左右两列：左侧模型，右侧参数
- [x] 单文件与批量区已拆开，批量按钮已回到首屏可操作区域
- [x] 结果预览与下载区已压缩，不再占用过宽空间

- [x] P2. 全站按钮分层已统一第一轮
- [x] 执行类按钮统一主色：开始识别、批量执行、重试失败项、开始流式识别、开始说话人分离、开始情感识别
- [x] 刷新/检查/下载类按钮统一次级色：刷新模型列表、检查服务、刷新运行日志、打包下载运行日志
- [x] 预留页按钮已按“执行主色 / 下载次级色”统一

- [x] P3. 各 Tab 文案已统一第一轮
- [x] `"开始 Streaming"` 已收口为 `"开始流式识别"`
- [x] `"Streaming 模型"`、`"Streaming 状态"`、`"Streaming 输出"` 已统一为中文
- [x] 说话人分离 / 情感识别页模型标签已统一

- [x] P4. 模型下拉兜底已修正
- [x] 保留“接口失败时回退静态模型清单”的策略
- [x] 已避免失败时只显示单模型的错误体验

- [x] P5. 已完成一轮回归验证
- [x] 已运行 `"tests/test_pat_webui_diarization_exports.py"`
- [x] 已运行 `"tests/test_pat_webui_utils.py"`

### 待你一次性确认

- [ ] C1. 是否继续把运行时控制区参数标签中文化
- [ ] 拟改为：`"device"` → `"运行设备"`、`"hub"` → `"模型来源"`、`"disable_update"` → `"禁用更新检查"`、`"ncpu"` → `"CPU 线程数"`、`"log_level"` → `"日志级别"`、`"disable_pbar"` → `"禁用进度条"`

- [ ] C2. 是否继续把功能参数改成“中文说明 + 技术参数名”
- [ ] 拟改为：`"chunk_size"` → `"分块大小(chunk_size)"`、`"encoder_chunk_look_back"` → `"编码器回看帧数(encoder_chunk_look_back)"`、`"decoder_chunk_look_back"` → `"解码器回看帧数(decoder_chunk_look_back)"`
- [ ] 拟改为：`"spk_model"` → `"说话人模型(spk_model)"`、`"spk_mode"` → `"说话人模式(spk_mode)"`、`"preset_spk_num"` → `"预设说话人数(preset_spk_num)"`、`"granularity"` → `"情感粒度(granularity)"`

- [ ] C3. 是否增加“模型来源状态提示”
- [ ] 当 `/v1/models` 可用时显示“当前为后端实时模型列表”
- [ ] 当 `/v1/models` 不可用时显示“当前为静态兜底模型列表”

### 你确认后立即执行

- [ ] E1. 统一运行时控制区标签
- [ ] E2. 统一流式识别 / 说话人分离 / 情感识别 参数标签格式
- [ ] E3. 增加模型列表来源状态提示
- [ ] E4. 同步更新 `"tests/test_pat_webui_diarization_exports.py"`、`"tests/test_pat_webui_utils.py"`
- [ ] E5. 运行回归：`python -m pytest "tests/test_pat_webui_utils.py" "tests/test_pat_webui_diarization_exports.py" -q`

### 执行顺序

- [ ] 先改布局与按钮层级
- [ ] 再统一文案与状态提示
- [ ] 最后补/改测试并做本地回归

### 默认执行原则

- [ ] 只改标签、提示、布局与样式，不改接口字段名，不改后端协议
- [ ] 优先使用“中文说明 + 技术参数名”的写法，兼顾可读性与排障能力
- [ ] 若某标签过长影响布局，则退回“中文短标签 + placeholder 说明”

## 范围边界

- 本轮先做本地可运行的 WebUI，不涉及部署、数据库迁移、外部服务调用等危险操作
- 本轮不改原 "app/openai_api/gradio_app.py" 的功能与入口
- 本轮优先支持单机本地调用现有 API，暂不把“官方所有能力”一次性做完

## 执行顺序

- [x] A0. 补最小化测试与验证骨架
- [x] A1. 搭建独立目录与启动入口
- [ ] A2. 打通基础转写链路
- [x] B1. 动态模型列表
- [x] B2. 多格式输出与下载
- [x] B3. 高级参数面板
- [x] B4. 批量与队列
- [x] B5. 第一版回归验证
- [x] C1. 技术路线决策
- [ ] C2. 官方增强能力逐项落地

## 当前目标

- [ ] 先完成 "Pat WebUI" MVP，可独立启动并完成一次真实转写
- [ ] 搭建 "Pat WebUI" 独立目录与启动入口
- [ ] 完成基础转写链路打通
- [ ] 补齐多格式输出与下载
- [ ] 增加高级参数面板
- [ ] 增加批量/队列能力
- [ ] 评估并落地“功能完整 WebUI”的技术路线

## 完成定义

- 阶段 A 完成：Pat WebUI 可独立启动，能调用现有 API 做一次真实转写
- 阶段 B 完成：Pat WebUI 具备模型列表、多格式下载、参数面板、批量任务四项基础能力
- 阶段 C 完成：至少完成 1 个非基础 ASR 能力的真实闭环，并有明确技术路线沉淀

## 阶段 A0：最小化测试与验证骨架

目标：先补最小测试与手工验证清单，满足“新功能先写测试，再实现”。

- [x] 新增 "tests/" 下与 Pat WebUI 相关的最小测试文件
- [x] 为请求体白名单拼装增加单测
- [x] 为响应格式到下载文件的映射逻辑增加单测
- [x] 为模型列表解析逻辑增加单测
- [x] 准备 1 份手工冒烟清单：启动、上传、转写、下载、报错展示（"Docs/smoke_pat_webui.md"）

验收：

- [x] `python -m unittest discover -s "tests"` 通过
- [x] 手工冒烟清单可覆盖 MVP 主流程

## 阶段 A：WebUI 分支隔离

目标：复制一套新的 "Pat WebUI" 入口，保证后续大改不影响现有 UI。

- [x] 新建目录："app/pat_funasr_webui/"
- [x] 复制现有 UI 入口到："app/pat_funasr_webui/gradio_app.py"
- [x] 如有必要，提取 Pat WebUI 自己的工具模块（如请求构建、响应保存、模型列表适配）
- [x] 新增启动脚本："run_ui_pat.bat"
- [x] 新增："FunASR_pat.bat"（同时启动 API + Pat WebUI）
- [x] 设定默认端口为 "7861"，避免与原 UI 冲突
- [x] 设定默认 API base_url 为 "http://localhost:8000"

验收：

- [ ] 运行 "run_ui_pat.bat" 后可打开 Pat WebUI
- [ ] Pat WebUI 能正常调用 API 完成一次转写

## 阶段 B：Pat WebUI 第一版

范围：只改 "app/pat_funasr_webui/gradio_app.py" 及其同目录新模块，不动原 "app/openai_api/gradio_app.py"。

### B0. 页面骨架与状态管理

- [x] 明确页面结构：基础转写区 / 高级参数区 / 结果预览区 / 下载区 / 批量区
- [x] 明确单文件与多文件两条处理流程
- [x] 明确错误展示区、运行状态区、结果缓存区
- [x] 删除顶部阶段性说明文案，避免无效占位信息干扰操作
- [x] 重构 "服务与调试" 页：展示运行概览、加载方式、模型语言覆盖、推荐入口与原始调试输出
- [x] 同步更新相关文档："README.md" / "Docs/README.md" / "Docs/model-capability-matrix.md" / "Docs/api.md" / "Docs/smoke_pat_webui.md"
- [x] 收口 "FunASR_pat.bat"：改为 Python 托管单窗口启动，关闭启动窗口时自动结束 API/UI 子进程

验收：

- [x] 页面结构已固定，后续功能项可直接挂载
- [x] 单文件与批量流程的状态流转清晰

### B1. 动态模型列表

- [x] 启动时调用 `GET /v1/models`
- [x] 下拉框改为动态模型列表
- [x] 显示模型 ready 状态
- [x] 增加“刷新模型列表”按钮

验收：

- [ ] 页面加载后可看到后端当前模型列表
- [ ] 点击刷新后模型列表可更新

### B2. 多格式输出与下载

- [x] 输出格式支持：txt / srt / vtt / tsv / json / verbose_json / all(zip)
- [x] 对非 JSON 响应保存为临时文件
- [x] 提供下载组件（File）
- [x] 提供预览组件（Textbox/Code）

验收：

- [ ] 可在 UI 中下载 txt / srt / vtt / tsv / zip
- [ ] 预览内容与下载文件一致

### B3. 高级参数面板

- [x] 基础模型参数：`model`
- [x] 输出参数：`response_format`
- [x] 在 Accordion 中暴露："vad_preset"
- [x] 在 Accordion 中暴露："merge_vad"
- [x] 在 Accordion 中暴露："merge_length_s"
- [x] 在 Accordion 中暴露："max_line_width"
- [x] 在 Accordion 中暴露："hotword"
- [x] 在 Accordion 中暴露："language"
- [x] 在 Accordion 中暴露："use_itn"
- [x] 请求体按白名单拼接，禁止 UI 任意字段透传

验收：

- [ ] UI 参数修改后可正确传到后端
- [ ] 白名单外参数不会被提交

### B4. 批量与队列

- [x] 支持多文件上传
- [x] 支持队列执行
- [x] 显示整体进度与单文件状态
- [x] 失败项保留错误详情
- [x] 支持失败项重试

验收：

- [ ] 多文件任务可顺序执行完成
- [ ] 单个文件失败不会中断整个批次

### B6. 批量卡死/前端内存优化

目标：解决超长音频（> 1h）或大批量场景下，WebUI 出现“页面超出内存/卡死/刷屏”的问题。

- [x] 限制预览输出长度：结果预览与调试 JSON 只展示尾部 N 字符，完整内容只走下载文件
- [x] 批量 results 只保存短摘要：不在内存中保存每个文件的全文 transcript/raw_content（避免单个超长结果把 UI 进程撑爆）
- [x] 批量状态刷新节流：减少 yield 次数（每 N 个文件或每 T 秒刷新一次），降低 Gradio 前端状态同步压力
- [x] Streaming 输出节流：避免每个 chunk 反复发送全量 full_text；只展示尾部预览 + 独立进度信息
- [ ] 大文件上传降峰（可选）：移除 `audio_path.read_bytes()` + `b"".join(parts)` 的双份拷贝，改为流式 multipart 发送

验收：

- [ ] 批量转写包含 \"test\\孙老师分享录音20250310.aac\" 与 \"test\\IBEC竞标会议录音.m4a\" 时，WebUI 不再出现浏览器 OOM
- [ ] Streaming 输出不再刷屏到不可用（进度展示稳定）
- [ ] UI 进程内存峰值明显下降（至少不随 transcript 全文线性累积）

### B5. 第一版回归验证

- [x] 单文件转写回归：txt / srt / vtt / tsv / json / zip（已覆盖 sensevoice / paraformer）
- [x] 多模型回归："sensevoice"
- [x] 多模型回归："paraformer"
- [x] 多模型回归："fun-asr-nano"
- [x] 异常回归：后端不可用、返回非 200、文件为空、格式不支持
- [x] 下载回归：文件名、扩展名、内容预览一致

验收：

- [ ] 第一版主路径均可重复通过
- [ ] 常见异常可见、可定位、不会导致页面状态错乱

## 阶段 C：对齐官方能力

说明：这一阶段是增强目标，需先确定技术路线，再逐项落地。

### C1. 技术路线决策

- [x] 明确采用哪条路线：（详见 "Docs/design/pat_webui_route_decision.md"）
  - 扩展后端 API，继续走 "/v1/audio/transcriptions"
  - 或让 Pat WebUI 直接调用 FunASR 推理链路
- [x] 记录选型原因、影响范围、接口变化
- [x] 记录与官方教程/官方 API 的对应关系，防止后续功能名和参数名跑偏

验收：

- [x] 有明确的一页式决策结果，能指导后续开发

### C2. 功能范围

- [ ] 离线识别（ASR）：Paraformer / SenseVoice / Fun-ASR-Nano / Qwen3-ASR
- [x] 流式识别（Streaming ASR）：新增后端 "/v1/funasr/streaming" + Pat WebUI Streaming 区
- [x] 说话人分离（Diarization）：新增后端 "/v1/funasr/diarization" + Pat WebUI 说话人分离区（MVP）
- [x] 说话人分离导出增强：Pat WebUI 页面支持直接下载 json / txt / srt / vtt / tsv / zip
- [x] 情感识别（Emotion）：新增后端 "/v1/funasr/emotion" + Pat WebUI 情感识别区（MVP）
- [x] 语音活动检测（VAD）：已并入离线识别页，支持预设 + 单段最大时长控制
- [x] 标点恢复（PUNC）：已并入离线识别页，支持按请求关闭外置 PUNC

### C3. 关键参数支持

- [x] 通用参数：model / device / hub / disable_update / ncpu / log_level / disable_pbar
- [x] ASR 参数：batch_size_s / hotword / language / use_itn / merge_vad / merge_length_s
- [x] VAD 参数：vad_kwargs.max_single_segment_time
- [ ] Streaming 参数：chunk_size / encoder_chunk_look_back / decoder_chunk_look_back / cache / is_final
- [x] Diarization 参数：spk_model / spk_mode
- [x] Emotion 参数：granularity

验收：

- [x] 至少完成 1 个非基础 ASR 能力的真实可用闭环

## 官方能力映射备注

- Paraformer：适合中文生产级识别，通常需配合 "fsmn-vad" 与 "ct-punc"
- Paraformer-Streaming：适合实时字幕，必须处理 `cache` 与 `is_final`
- Fun-ASR-Nano：当前 README_zh 模型表格写“中文 / 英文 / 日文”，并强调中文 7 大方言与 26 种地域口音；内置标点，后续可重点考虑国际化场景
- SenseVoice：除识别外还带情感/事件标签，适合作为增强能力优先候选
- Qwen3-ASR：精度高但链路更重，建议放在增强阶段而非 MVP 阶段
- Emotion2vec：已作为独立情感能力接入 MVP，当前先支持整体情感排序；时间片能力后续继续增强

## 依赖与前提

- [ ] 现有 "/v1/models" 返回结构稳定，可供 UI 动态读取
- [ ] 现有 "/v1/audio/transcriptions" 可稳定返回 txt / srt / vtt / tsv / json / all(zip)
- [ ] 现有原始 WebUI 可作为 Pat WebUI 的复制基线
- [ ] 测试目录与最小样例文件可用于冒烟验证

## 风险与待确认

- [ ] 确认 Pat WebUI 是否允许直接绕过 OpenAI-Compatible API
- [ ] 确认完整能力优先级，避免一次性把范围拉得过大
- [ ] 确认是否需要新增独立的后端适配层
- [ ] 确认 Gradio 组件是否足够承载流式识别交互；若不足，需提前调整实现方式
- [ ] 确认 Qwen3-ASR、说话人分离、情感识别是否要一起进入第一轮增强范围
- [x] UI 规划口径：只有独立工作流拆 Tab；VAD / PUNC / batch_size_s 等强相关参数并入原页面

## 已完成归档

以下事项已完成，仅保留摘要，避免继续污染当前执行清单：

- [x] 输出渲染增强：支持 txt / srt / vtt / tsv / all(zip)
- [x] 时间戳兜底与字幕分段优化
- [x] API 扩展：response_format 与参数白名单
- [x] 跑批脚本修复："run_test_all_models.ps1"
- [x] 文档同步："Docs/api.md"、"Docs/model-capability-matrix.md"
- [x] 冒烟校验与测试通过
