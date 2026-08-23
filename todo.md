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

- [x] 阶段 A：Pat WebUI 分支隔离
- [x] 阶段 B：Pat WebUI 第一版

## 当前专项计划：FunASR 官方文档对齐与稳定性优化

说明：本专项只围绕官方教程、API 文档与官方仓库口径做小步优化；不部署、不下载模型、不安装依赖、不执行数据库操作，不自动 git push/merge/pull。

### 关键假设

- [ ] 当前项目继续保留自研 OpenAI-Compatible API + Pat WebUI，不切换为官方 `funasr-server` 主入口
- [ ] 默认继续优先使用 ModelScope hub=`ms` 与本地缓存，避免默认访问 HuggingFace
- [ ] 先做不需要真实模型加载的静态配置、参数、文档与单测；真实模型转写实测单独确认后再跑
- [ ] FunASR vendored 代码当前版本为 `1.3.9`，本轮只记录上游同步策略，不直接升级 vendored 源码

### 可验证完成标准

- [ ] SenseVoice 默认不再挂外置 `punc_model="ct-punc"`，Paraformer 系仍保留标点模型
- [ ] `_dbg_report()` 默认不发送本地调试事件，只有显式环境变量开启时才发送
- [ ] `/v1/audio/transcriptions` 支持白名单参数 `batch_size_threshold_s`，并透传给 FunASR `generate()`
- [ ] `Docs/api.md`、`Docs/model-capability-matrix.md`、静态 `openapi.json` 与实际 API 参数/模型列表一致
- [ ] 单元测试覆盖上述行为，不触发模型下载或真实推理
- [ ] 幂等验证通过：相关 pytest 用例全部 PASS

### 任务 1：修正 SenseVoice 默认标点模型配置

- [x] 修改文件：`"app/openai_api/server.py"`
- [x] 修改点：从 `MODEL_CONFIGS["sensevoice"]` 中移除 `punc_model`
- [x] 保留点：`MODEL_CONFIGS["paraformer"]` 与 `MODEL_CONFIGS["paraformer-zh-streaming"]` 继续保留 `punc_model="ct-punc"`
- [x] 同步文件：`"scripts/prefetch_models.py"`、`"scripts/batch_transcribe.py"` 中的 `sensevoice` 配置保持一致
- [x] 先改测试：`"tests/test_model_configs.py"` 增加断言，确认 `sensevoice` 没有 `punc_model`，`paraformer` 仍有
- [x] 验证命令：`python -m pytest "tests/test_model_configs.py" -q`

### 任务 2：调试上报增加默认关闭开关

- [x] 修改文件：`"app/openai_api/server.py"`
- [x] 修改点：`_dbg_report()` 开头判断环境变量，例如 `FUNASR_DEBUG_REPORT=1` 时才继续发送事件
- [x] 默认行为：未设置环境变量时直接 return，不访问 `127.0.0.1:7777`
- [x] 先改测试：新增或扩展 server 纯函数测试，验证默认关闭与显式开启的分支
- [x] 验证命令：`python -m pytest "tests/test_server_generate_kwargs.py" -q`

### 任务 3：新增 `batch_size_threshold_s` 参数白名单

- [x] 修改文件：`"app/openai_api/server.py"`
- [x] 修改点：`transcribe()` 增加表单字段 `batch_size_threshold_s: Optional[int]`
- [x] 修改点：`build_generate_kwargs()` 增加入参并校验 `> 0`
- [x] 透传规则：用户传入时设置 `generate_kwargs["batch_size_threshold_s"] = int(batch_size_threshold_s)`
- [x] 错误规则：`batch_size_threshold_s <= 0` 返回 HTTP 400，错误信息清晰
- [x] 先改测试：`"tests/test_server_generate_kwargs.py"` 覆盖正常透传与非法值
- [x] 验证命令：`python -m pytest "tests/test_server_generate_kwargs.py" -q`

### 任务 4：同步 Pat WebUI 参数透传

- [x] 修改文件：`"app/pat_funasr_webui/gradio_app.py"`
- [x] 修改点：高级参数区增加 `batch_size_threshold_s` 数值输入
- [x] 修改点：请求后端时带上 `batch_size_threshold_s`
- [x] 修改文件：`"app/pat_funasr_webui/app_utils.py"`
- [x] 修改点：参数归一化白名单加入 `batch_size_threshold_s`
- [x] 先改测试：`"tests/test_pat_webui_utils.py"` 覆盖该字段的数值归一化
- [x] 验证命令：`python -m pytest "tests/test_pat_webui_utils.py" -q`

### 任务 5：更新 API 文档与 OpenAPI

- [x] 修改文件：`"Docs/api.md"`
- [x] 修正点：`models_available` 示例补齐当前实际模型：`sensevoice / paraformer / paraformer-en / paraformer-zh-streaming / fun-asr-nano / qwen3-asr / qwen3-asr-0.6b / emotion2vec-plus-large`
- [x] 修正点：说明 SenseVoice 不默认挂外置 `punc_model`
- [x] 新增点：记录 `batch_size_threshold_s`
- [x] 修改文件：`"Docs/model-capability-matrix.md"`
- [x] 修正点：补充本轮配置差异与官方文档对齐说明
- [ ] 修改文件：`"app/openai_api/openapi.json"`（需后续验证是否已同步）
- [x] 验证命令：`python -m pytest "tests/test_server_transcriptions_endpoint.py" "tests/test_server_streaming_endpoint.py" "tests/test_server_emotion_endpoint.py" "tests/test_server_diarization_endpoint.py" -q`

### 任务 6：新增上游同步说明

- [x] 创建文件：`"Docs/upstream-sync.md"`
- [x] 内容包括：当前 vendored FunASR 版本、官方教程链接、官方 API 链接、官方仓库链接、同步策略、升级前验证清单
- [x] 注意：文档使用 UTF-8 BOM 与 Windows CRLF
- [x] 同步索引：更新 `"Docs/README.md"` 增加该文档入口
- [x] 验证命令：`python -m pytest "tests/test_model_configs.py" -q`

### 任务 7：最终回归与收尾

- [ ] 执行幂等测试：`python -m pytest "tests/test_model_configs.py" "tests/test_server_generate_kwargs.py" "tests/test_pat_webui_utils.py" "tests/test_server_transcriptions_endpoint.py" "tests/test_server_streaming_endpoint.py" "tests/test_server_emotion_endpoint.py" "tests/test_server_diarization_endpoint.py" -q`
- [ ] 检查 Git 状态：`git status --short`
- [ ] 汇总改动文件、验证结果、未执行事项
- [ ] 如需 commit，先向用户确认提交范围与 commit message

### 暂不执行事项

- [ ] 不真实加载 Qwen3-ASR / Fun-ASR-Nano / SenseVoice 大模型
- [ ] 不下载模型、不安装 `qwen-asr`、不访问外部 API
- [ ] 不升级 `app/funasr` vendored 源码
- [ ] 不新增 WebSocket 流式接口；该项后续可作为独立专项
- [ ] 不改部署脚本与 Docker/Kubernetes 配置，除非后续明确要求

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

- [x] C1. 是否继续把运行时控制区参数标签中文化
- [x] 拟改为：`"device"` → `"运行设备"`、`"hub"` → `"模型来源"`、`"disable_update"` → `"禁用更新检查"`、`"ncpu"` → `"CPU 线程数"`、`"log_level"` → `"日志级别"`、`"disable_pbar"` → `"禁用进度条"`

- [x] C2. 是否继续把功能参数改成“中文说明 + 技术参数名”
- [x] 拟改为：`"chunk_size"` → `"分块大小(chunk_size)"`、`"encoder_chunk_look_back"` → `"编码器回看帧数(encoder_chunk_look_back)"`、`"decoder_chunk_look_back"` → `"解码器回看帧数(decoder_chunk_look_back)"`
- [x] 拟改为：`"spk_model"` → `"说话人模型(spk_model)"`、`"spk_mode"` → `"说话人模式(spk_mode)"`、`"preset_spk_num"` → `"预设说话人数(preset_spk_num)"`、`"granularity"` → `"情感粒度(granularity)"`

- [x] C3. 是否增加“模型来源状态提示”
- [x] 当 `/v1/models` 可用时显示“当前为后端实时模型列表”
- [x] 当 `/v1/models` 不可用时显示“当前为静态兜底模型列表”

### 你确认后立即执行

- [x] E1. 统一运行时控制区标签
- [x] E2. 统一流式识别 / 说话人分离 / 情感识别 参数标签格式
- [x] E3. 增加模型列表来源状态提示
- [x] E4. 同步更新 `"tests/test_pat_webui_diarization_exports.py"`、`"tests/test_pat_webui_utils.py"`
- [x] E5. 运行回归：`python -m pytest "tests/test_pat_webui_utils.py" "tests/test_pat_webui_diarization_exports.py" -q`

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
- [x] A2. 打通基础转写链路
- [x] B1. 动态模型列表
- [x] B2. 多格式输出与下载
- [x] B3. 高级参数面板
- [x] B4. 批量与队列
- [x] B5. 第一版回归验证
- [x] C1. 技术路线决策
- [x] C2. 官方增强能力逐项落地

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
- [x] Streaming 参数：chunk_size / encoder_chunk_look_back / decoder_chunk_look_back / cache / is_final
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
## 当前专项计划：Gradio 原生流式 Mic 重写

说明：本专项已完成，正式“流式识别”页回到 Gradio 原生组件实现，不再在正式页自绘设备选择器或跳转 `/mic-stream`。

### 已完成

- [x] 正式页 Mic 实时识别使用 `gr.Audio(sources=["microphone"], type="numpy", streaming=True)` 与 `.stream(...)`
- [x] 文件流式识别与 Mic 实时识别保持左右两栏，状态、输出、下载互相独立
- [x] 移除正式页自定义设备 HTML/JS、`getUserMedia` 注入与 `/mic-stream` 正式入口
- [x] Mic 开始录制前检查 `/v1/models/{model}/status`，必要时调用 `/v1/models/{model}/load`
- [x] 修复 Gradio numpy int16 双声道音频被错误放大导致削波的问题
- [x] 流式预览默认单段连续显示，不再按短句自动换行
- [x] 增加正式页配置与音频转换相关测试

### 验证

- [x] `python -m pytest "tests\test_pat_webui_diarization_exports.py" -q`
- [x] 浏览器打开 `http://127.0.0.1:7861/`，进入“流式识别”，确认 Mic 区为 Gradio 原生控件，且无 `patFormalMicDeviceSelect` / `patchedFormalGetUserMedia` / `/mic-stream`

## 2026-06-11 回归审计：实时 Mic 与时间戳

### 已确认

- [x] 当前分支相对 `origin/main` 超前 20 个提交；存在 3 个未跟踪项，审计期间不修改
- [x] 项目测试 `runtime\python\python.exe -X utf8 -m pytest "tests" -q`：147 passed
- [x] 使用真实音频按 Mic PCM 分片调用 `/v1/funasr/streaming`，后端逐片返回识别文本
- [x] 直接调用 `stream_transcribe_microphone`，信号、状态与累计识别文本均正常
- [x] 当前运行时 Gradio Mic 配置包含 `sources=["microphone"]`、`type="numpy"`、`streaming=true`
- [x] 自动化浏览器环境没有可用麦克风设备，无法代替真实设备完成端到端录音验收

### 发现的问题

- [x] P0：`segmentation._to_seconds()` 把 1000-9999ms 当成秒，已通过失败测试复现并恢复 FunASR 毫秒转换
- [x] P1：对照 `4496919` 恢复首次渲染即启用的 Gradio 原生 Mic，移除运行时 `interactive` 更新与 Audio 组件重建
- [x] P1：恢复 `start_recording` 中的模型 ready 检查与会话初始化
- [ ] P1：现有测试只覆盖 Python 回调和静态配置，未覆盖浏览器设备枚举、首次录制、停止后再次录制
- [ ] P2：多处 `gr.update(...)` 被替换为新组件实例，改动面较大，需要按实际交互逐项回归，避免组件状态被重建
- [ ] P2：`trust_remote_code=True` 的适用模型和安全边界需要重新核对
- [ ] P3：裸跑 `pytest -q` 会收集便携 Python 的第三方包测试，建议增加项目级 `pytest.ini` 限定 `testpaths=tests`

### 建议执行顺序

- [x] 先为时间戳回归和 Mic 模型就绪状态补失败测试
- [x] 恢复 `4496919` 的 Mic 录制事件与模型初始化顺序
- [x] 以已验证可用的 `aipython/gradio_streaming_asr_test.py` 为基线，保持正式页全 Gradio 原生组件
- [ ] 补首次录制、停止后重录、有效信号、累计文本的浏览器验收记录
- [ ] 再审查 `gr.update` 迁移与 `trust_remote_code`，避免与 Mic 修复混在同一改动中

### 完成标准

- [x] 运行时配置与 `4496919` 对齐：Mic 初始可交互，且没有回调更新 Audio 组件
- [ ] 首次点击录制即可收到有效信号并持续显示识别结果
- [ ] 停止后再次录制可正常创建新会话并识别
- [x] 1500ms 正确转换为 1.5s，相关测试通过
- [x] 全量测试 147 项、编译检查与正式页运行时配置检查通过

## 2026-06-12 跨语言翻译 Tab 专项计划

### 1. 目标与范围
- 在 Gradio WebUI 中新增一个“跨语言翻译” Tab。
- 采用 GPU 显存统一管理策略：在后端 API 服务（`"app/openai_api/server.py"`）中提供统一的翻译端点 `/v1/translations`，而在 WebUI（`"app/pat_funasr_webui/gradio_app.py"`）中仅执行 HTTP 路由调用。
- 模型支持：支持 `nllb-200-distilled-600m` 与 `nllb-200-distilled-1.3b` 两款机器翻译专用模型。
- 源与目的语言：首选支持 **中（简体/繁体）、英、日、韩、法、泰、马来、越南** 8 种主要语言。
- 输入与输出：
  - 支持直接长文本框输入，或上传文本类文件（`.txt`, `.md`, `.srt`, `.vtt`, `.tsv`, `.json`）。
  - 对字幕格式（SRT/VTT）需要进行格式防打乱解析（只翻译文本，保留时间戳）。
  - 右侧显示翻译结果文本框，并提供翻译后同格式文件的打包下载。

### 2. 执行计划步骤

- [x] **阶段 T1：后端翻译推理链路与接口实现**
  - [x] 在 `"app/openai_api/server.py"` 中新增 `MODEL_CONFIGS` 配置，支持对 `nllb-200-distilled-600m` 和 `nllb-200-distilled-1.3b` 的加载管理。
  - [x] 适配 HuggingFace (`hf`) 与 ModelScope (`ms`) 的模型下载及加载逻辑，兼容本地缓存。
  - [x] 新增 `POST /v1/translations` 路由，接口定义：
    - 输入：`text` (待翻译文本或文本列表)，`source_lang` (源语言)，`target_lang` (目标语言)，`model` (模型名)。
    - 输出：`{"translated_text": "..."}`。
  - [x] 编写对应的翻译服务加载与句级别推理（nllb 推理防超长）的异常处理。
  - [x] **先写测试**：在 `"tests/"` 目录下新增 `"test_server_translation_endpoint.py"` 验证 API 输入输出协议，且在无真实模型时走 Mock 推理。

- [x] **阶段 T2：格式解析器（Parser）开发**
  - [x] 在 `"app/pat_funasr_webui/app_utils.py"` 或新建模块中开发字幕解析与还原逻辑：
    - 对 `.srt` / `.vtt`：精准解析行、时间戳、序号，将文本发送给翻译接口，然后再按格式组装回。
    - 对 `.json` / `.tsv`：解析文本字段翻译。
    - 对 `.txt` / `.md`：做超长文本（> 512 tokens）的按句/段安全切分，分批翻译后再拼接。
  - [x] **先写测试**：新增字幕格式解析与防打乱重组的单元测试，确保各类文件处理正常。

- [x] **阶段 T3：Gradio WebUI 界面开发与绑定**
  - [x] 在 `"app/pat_funasr_webui/gradio_app.py"` 中增加“跨语言翻译” Tab。
  - [x] 界面排版：左右分栏，左侧为参数选择（源语言、目标语言、模型、选择 hf/ms）、长文本框输入、文件上传区、开始翻译按钮；右侧为翻译结果框、下载文件组件。
  - [x] 绑定按钮 click 事件：根据输入是“文本框”还是“上传文件”自动流转，处理完毕后展示并生成下载文件。
  - [x] **先写测试**：补齐 UI 点击事件与参数流转的集成测试。

- [x] **阶段 T4：最终回归与实机部署验证**
  - [x] 用 600M 模型进行本地小步真实翻译实测，验证中、英、日、韩、法、泰、越、马的翻译正确性。
  - [x] 全量回归测试通过。
- [x] **阶段 T5：全量 NLLB-200 语言选择扩展（202种语言）**
  - [x] 提取模型 Tokenizer 的特殊特殊语言标识（`ace_Arab` 到 `zul_Latn` 共计 202 种）。
  - [x] 创建独立的 `translation_languages.py` 提供排序与中文本地化名称字典，避免前后端重复代码。
  - [x] 前端 UI 下拉框 `choices` 升级为 202 种对照名，常用语言在前，其余字母排序。
  - [x] 后端 `server.py` 去除 9 种限制，引入动态字典，从白名单中彻底放开到 202 种。
  - [x] 补齐端点校验单元测试并回归测试成功。
- [x] **阶段 T6：翻译 Tab 排版深度优化**
  - [x] 重构 UI 布局：参数选择区（翻译模型、语言选择、高级生成参数、上传文件、开始翻译按钮）全部提到最上方左右分栏排布。
  - [x] 优化文本框布局：原文长文本输入框与译文翻译结果框左右对齐、等高（高度固定为 20 行）。
  - [x] 优化下载区域：翻译结果下载框移至最下方，界面逻辑更流畅直观。
  - [x] 重启应用服务：杀掉旧有 UI 服务进程并热重启 `FunASR_pat.bat`，加载全新语言列表及 UI 布局。
- [x] **阶段 T7：按需生成下载文件与中文标点符号转换**
  - [x] 重构下载流程：开始翻译按钮只负责完成翻译，增加“📊 生成并导出文件”按钮，点击后才从 `gr.State` 缓存的路径（文件翻译）或实时文本（文本翻译）动态生成下载文件，完美解决由于自动导出导致的 processing 悬挂卡死问题。
  - [x] 新增标点转换功能：在 UI 中增加“自动替换为中文全角标点”的 Checkbox 选项。
  - [x] 实现标点自动转换算法：在 `translation_utils.py` 中实现 `convert_to_chinese_punctuation` 逻辑，在翻译完后把英文半角符号（, . ? ! : ; " ()）一键替换为中文全角符号，并贴心排除数字小数点（3.14）、时间冒号（12:30）、和 URL（http://），并自动剔除全角标点后紧挨的英文空格。
  - [x] 补充标点测试：在 `test_pat_webui_translation_utils.py` 中补充相关单测，验证转换精度与格式保护，并通过测试。
  - [x] 再次热重启服务：杀死旧的 UI 服务进程并热重启 `FunASR_pat.bat`，使新 UI 功能立即生效。


- [x] **阶段 T8：体验优化与错误修复**
  - [x] 修复 `safe_translate_with_exports` 中因异常作用域导致的 `UnboundLocalError: local variable 'gr' referenced before assignment` 报错。
  - [x] 移除已废弃的 `TRANSFORMERS_CACHE` 环境变量定义，统一使用 `HF_HOME`，避免启动时的弃用警告。
  - [x] 在 `server.py` 中加载 NLLB 时自动过滤由于没有 Flash Attention 环境带来的 `UserWarning`。
  - [x] 将 `beam_search` 默认宽度和 WebUI 滑块最大值统一调整为 5，以平衡性能与翻译质量。
  - [x] 丰富生成的临时导出文件名，格式追加源语言、目标语言的短代码与时间戳（如：`原名_zh_en_20260612_170000.txt`）。

## 2026-06-12 Mic 流式收音重建

- [x] 检查当前 Git 状态及 Mic 相关提交历史，定位历史回归点。
- [x] 对照 Gradio 官方流式输入、实时语音识别示例和 FunASR 官方流式协议。
- [x] 确认故障边界位于浏览器音频进入 Gradio 回调的链路，早于 FunASR 推理。
- [x] 确认采用“Gradio 官方原生音频流 + 同步回调”的修复方案。
- [x] 先补充音频块归一化、回调返回类型、会话状态和组件配置测试。
- [x] 仅修复 Mic 流式回调调度方式，保持现有 UI 布局、文字和样式不变。
- [x] 使用项目运行时完成 45 项 WebUI 测试和 161 项全量回归测试。
- [x] 浏览器检查流式识别页面，确认 Mic 组件及页面布局未发生变化。
- [x] 根据真实麦克风反馈否定“仅同步回调即可恢复收音”的假设。
- [x] 恢复历史可用提交和 Gradio 官方示例使用的 `gr.Audio(sources=["microphone"])` 采集组件。
- [x] 验证服务端实际下发 `streaming=true` 且不再强制 `format=wav`，UI 布局保持不变。
- [x] 检查 Gradio 6.15.2 前端实现，确认原生设备下拉框未把选择值传入 `getUserMedia`。
- [x] 复用 7872 已验证思路，增加隐藏设备桥接，让现有下拉框选择和系统默认设备真正生效。
- [x] 运行态确认桥接脚本已安装，页面仍只有一个原生设备下拉框，浏览器控制台无错误。
- [x] 根据"一度有声后持续近静音"的日志确认事件流未中断，问题位于浏览器音频轨道。
- [x] 默认设备不再使用精确 `default` 伪设备约束，仅对明确选择的物理设备应用精确 ID。
- [x] 关闭浏览器 AEC、降噪和自动增益，固定单声道，并增加轨道 mute/unmute/ended 诊断。
- [x] 优化音频信号显示：峰值/RMS 改为百分比 + ASCII 音量条可视化。
- [ ] 在具有真实麦克风设备的浏览器中验证首次及重复录音的峰值、RMS 和识别结果。

## 合并麦克风状态显示 + 波形幅度优化

- [x] 合并 mic_status 和 mic_signal_status 为一个组件，2行显示
- [x] 优化 Gradio 麦克风波形显示幅度（CSS/JS 放大）

## 2026-06-13 Qwen3-ASR 离线字幕时间轴修复

- [x] 核对 Qwen3-ASR 官方强制对齐输出与 FunASR 适配链路
- [x] 先补适配层结构化时间戳测试并验证失败
- [x] 先补中英文句级时间轴与异常回退测试并验证失败
- [x] 在 `"app/funasr/models/qwen3_asr/model.py"` 保留字词文本及秒级时间戳
- [x] 在 `"app/openai_api/segmentation.py"` 按原生标点与字词顺序生成句级时间轴
- [x] 运行定向测试、API 回归测试与全量测试（167 项通过）

## 2026-08-23 项目优化、升级与问题审查

当前状态：离线审查已完成；未获批联网，因此最新版本与安全公告未核对。

### 审查范围与假设

- [x] 盘点项目结构、启动链路、依赖声明、配置、测试、文档与当前 Git 状态。
- [x] 重点审查 `app/openai_api/`、`app/pat_funasr_webui/`、`aipython/`、`scripts/`、`tests/` 及根目录启动脚本。
- [x] 区分项目自研代码、上游 FunASR 镜像代码与运行时/模型文件，避免把上游大目录的普通技术债误判为本项目缺陷。
- [x] 保留当前工作区已有未提交修改，不覆盖、不回退、不提交。
- [x] 本轮只做诊断与建议，不直接修改业务代码；后续修复需按优先级另行确认。
- [x] 如需核对依赖最新版本、上游公告或安全通告，先说明联网目的并获得确认。

### 计划步骤

- [x] 建立当前实现与运行链路地图，识别重复入口、过期脚本、配置漂移和文档偏差。
- [x] 静态审查 API、WebUI、LLM 精细转录、音频处理、导出与辅助脚本的正确性、安全性、性能和可维护性。
- [x] 检查依赖来源、版本固定方式、兼容边界及可升级项。
- [x] 说明测试、语法检查及其他幂等校验的目的，经确认后运行，并记录通过、失败与未覆盖项。
- [x] 输出 `Docs/project-audit-20260823.md`，按 P0/P1/P2/P3 标注证据、影响、建议修复、验证办法与升级风险。

### 可验证完成标准

- [x] 每个确认问题包含文件路径和代码位置、可复现或可静态验证的证据、实际影响，避免仅给泛化建议。
- [x] 依赖升级项包含当前版本/来源、目标建议、兼容风险和回退思路；未联网时明确标记“未核对最新版本”。
- [x] 报告覆盖功能正确性、安全、性能、稳定性、可维护性、测试、文档和部署/启动链路。
- [x] 报告明确区分“必须修复”“建议优化”“可选升级”和“需要运行环境验证”。
- [x] 最终汇报列出检查文件、验证结果、未执行事项，并说明现有未提交改动未被覆盖。

## 2026-08-23 后端能力整合与会议精细转录规划

当前状态：详细分析和正式规划文档已完成；等待用户确认是否进入业务代码实施阶段。

执行口径：`todo.md` 仅用于登记任务边界、执行步骤和完成状态；本专项的正式规划交付物及后续开发依据是 `Docs/backend-consolidation-and-fine-transcription-plan-20260823.md`。规划文档生成并确认后，再把其中获批的实施任务同步回 `todo.md` 跟踪执行，避免同时维护两套冲突计划。

### 范围与假设

- [x] Docker 本轮仅忽略，不删除 Dockerfile、Compose 或 Kubernetes 文件；如后续需要删除，另行确认精确目标。
- [x] 当前前端共有 8 个 Tab：离线识别、流式识别、说话人分离、情感识别、跨语言翻译、音频工具、精细转录、服务与调试。
- [x] 当前后端共有转写、流式、情感、说话人分离、模型列表/状态/加载、翻译、Mic 页面和健康检查等入口。
- [x] 区分“前端体验可以合并”“后端接口可以复用”“底层服务模块必须共享”三种层级，避免为了合并而制造巨型接口。
- [x] 保留 OpenAI-compatible `/v1/audio/transcriptions` 契约，不用新接口破坏已有客户端。
- [x] 本轮只形成规划文档，不修改接口、模型、Tab 或精细转录代码。
- [x] 先基于当前仓库与本地版本分析；未获批联网时，不声称模型最新能力已完成官方在线复核。

### 已确认的关键问题

- [x] 精细转录当前只调用 `/v1/audio/transcriptions`，没有复用现成的 `/v1/funasr/diarization`。
- [x] 场景模板中的 `diarization=True` 会被作为额外表单字段发给转写接口，但转写接口没有该参数，因此不会触发说话人分离。
- [x] 核对独立说话人分离端点的 ASR、VAD、CAM++、时间戳和 speaker 对齐链路，判断哪些能力可直接复用。
- [x] 分析“一次请求完成转写+说话人分离”与“分阶段 ASR→diarization→对齐”的准确率、资源、失败隔离和复用差异。
- [x] 分析多模型转写校对方案，包括时间轴对齐、候选冲突、置信度、语言/模型路由、LLM 受约束校对和成本控制。

### 用户补充的精细化选型原则

- [x] 精细转录的所有可选阶段都在前端列出，不由后端静默启用：音频前处理、VAD/切块、单模型或多模型转录、说话人分离、时间轴对齐、文本校对、LLM 优化、纪要、思维导图和导出。
- [x] 前端提供“推荐预设 + 高级自定义”两层入口；预设只负责填入推荐值，最终选项仍由用户确认和修改。
- [x] 多模型转录由用户明确选择主转录模型和一个或多个校对模型，不自动替用户决定模型。
- [x] 模型选择器展示能力标签、语言范围、说话人/时间戳支持、预计显存/速度风险和当前下载/加载状态。
- [x] 对有依赖关系的流程做前端联动校验，例如关闭时间戳时禁止需要时间对齐的多模型校对，关闭说话人分离时隐藏 speaker 参数。
- [x] 执行前展示完整配置摘要和预计阶段顺序，用户可以返回修改；运行中按阶段显示当前模型、进度、降级和失败原因。
- [x] 后端接收显式 workflow 配置，不擅自补开高成本步骤；仅对缺失/冲突配置做校验、默认值填充和安全上限约束。

### 计划步骤

- [x] 建立 Tab → UI 函数 → API → 底层模型/工具 → 导出/存储的完整映射。
- [x] 识别重复实现：上传与临时文件、模型配置/加载、音频前处理、转写、说话人分离、导出、任务状态、LLM 调用和错误处理。
- [x] 给出“保留独立端点 + 共享服务模块”与“新增统一编排端点”的边界建议。
- [x] 设计会议精细转录推荐链路，明确 speaker 标签在哪一步生成、如何与字词时间戳对齐、失败时如何降级。
- [x] 设计可选的双模型/多模型校对模式，明确默认模式、高质量模式和资源上限。
- [x] 输出 `Docs/backend-consolidation-and-fine-transcription-plan-20260823.md`，包含现状、目标架构、接口草案、模块复用、阶段任务、测试和验收标准。

### 可验证完成标准

- [x] 每个 Tab 都能映射到唯一的主能力与共享底层模块，重复功能有明确去留决定。
- [x] 规划明确哪些接口保留、合并、改为内部服务或新增编排层，并说明兼容性影响。
- [x] 明确回答说话人识别是否能与转写一次完成、当前为何没有生效、推荐采用哪条链路。
- [x] 多模型校对方案包含模型角色、时间轴对齐方法、冲突规则、LLM 边界、性能成本与失败降级。
- [x] 文档包含前端精细化选型信息架构、组件清单、字段依赖、校验规则和运行前配置摘要示例。
- [x] 多模型配置明确主模型、校对模型列表、模型顺序/权重、最多并发数、显存不足时的串行执行和用户可见降级策略。
- [x] 实施阶段遵循先测试、再共享模块、再接口编排、最后 UI 接入，且不要求一次性重构 4,000 行前端。
- [x] 文档使用 UTF-8 BOM + CRLF，无替换字符，最终汇报列出未执行事项。

### 2026-08-23 补充修订：运行状态中心与模型服务页

当前状态：正式规划文档已完成补充修订；用户已授权进入业务代码实施，按阶段测试后继续推进。

- [x] 在“转录工作台”规划持续可见的实时状态窗口，不能只在单行 Textbox 中覆盖最后一条状态。
- [x] 状态窗口同时提供总体进度、当前阶段、当前模型、分块/模型序号、已耗时、可用时的预计剩余时间，以及按时间追加的事件日志。
- [x] 统一事件级别与字段：`info/progress/success/warning/error`、`stage`、`model`、`message`、`error_code`、`retryable`、`trace_id`、`timestamp`。
- [x] 明确上报阶段：上传、媒体探测、前处理、VAD/切块、模型加载、主模型转录、校对模型转录、diarization、时间轴对齐、多模型校对、LLM、纪要、导出和存储。
- [x] 前端支持自动滚动、暂停滚动、按级别筛选、复制/下载日志、取消任务；只有后端声明可重试时才显示重试阶段/任务。
- [x] 错误展示同时包含用户可理解的信息和可追踪错误码；默认脱敏文件路径、Token、Header 和 Prompt，不直接暴露内部堆栈。
- [x] “模型与服务”重组为五个清晰区域：服务概览、模型管理、运行资源、任务队列、诊断与日志。
- [x] 模型管理以统一 model catalog 为来源，提供能力/语言/角色筛选、下载/加载状态、当前设备和资源风险；下载、切换来源、卸载等操作不静默执行。
- [x] 运行资源单独展示 CPU/RAM/GPU/显存、已加载模型、并发上限和当前占用；没有可靠数据时明确显示“不可用”，不伪造估算值。
- [x] 任务队列展示运行中/等待中/完成/失败任务及其当前阶段，并复用转录状态事件，不再单独维护另一套状态。
- [x] 原始健康 JSON、模型 JSON、运行日志和高级调试参数统一放入默认折叠的“诊断与日志”，与日常模型操作分离。
- [x] 修订 `Docs/backend-consolidation-and-fine-transcription-plan-20260823.md`，补充状态事件协议、状态窗口线框、模型与服务信息架构、组件去留和验收标准。

#### 补充修订完成标准

- [x] 用户无需打开后台日志即可定位任务卡在哪个阶段、使用哪个模型以及是否可重试。
- [x] warning 与 error 不会被后续 progress 覆盖，任务结束后仍可查看和下载完整事件记录。
- [x] 状态窗口显示内容与后端 job/stage event 一致，不由前端猜测进度或错误原因。
- [x] 模型选择、加载状态、资源监控、任务状态和调试信息职责分区明确，同一字段只有一个事实来源。
- [x] 文档修订后保持 UTF-8 BOM + CRLF，无替换字符，并列出本轮仍未执行的业务改动。

### 2026-08-23 实施完成记录

- [x] 阶段 A：统一模型目录，修复前处理、diarization 路由、参数状态码和精细管线测试。
- [x] 阶段 B：实现分块上传上限、single-flight 模型加载、工作流临时目录 TTL 和统一产物服务。
- [x] 阶段 C：实现严格 workflow schema、依赖校验、异步任务、追加式事件、取消和产物下载接口。
- [x] 阶段 D：实现独立 speaker 时间轴、时间重叠对齐、候选与不确定标记。
- [x] 阶段 E：实现用户自选主/校对模型、串并行策略、失败策略、主模型优先和加权共识审计结果。
- [x] 精细转录页列出全部可选流程和模型角色，并接入实时状态/错误窗口与取消按钮。
- [x] 模型与服务页重组为服务总览、模型管理与能力、运行资源、工作流任务队列、诊断与日志五区。
- [x] 修复长音频重叠重复、LLM 熔断计时、思维导图脚本注入/CDN 和同步推理阻塞事件循环。
- [x] 增加 CI、PR 模板、pytest 测试目录与预期第三方警告过滤。
- [x] 全量测试：`214 passed, 3 subtests passed`；`compileall` 通过。
- [x] 真实链路冒烟：从仓库 65 分钟 AAC 截取 60 秒，SenseVoice 主模型、Paraformer 校对、加权共识、Paraformer+CAM++ speaker 时间轴、对齐、17 条事件和 4 个产物均完成。
- [x] 修复真实冒烟发现的 `ClusterBackend` 未定义：便携运行时缺少 sklearn 时改用 NumPy 后备聚类，并优先解析本地模型缓存避免重复联网检查。
- [ ] 真实会议音频质量基线：仓库有真实音频但没有人工标注集；仍需至少 12 段标注样本评估 CER/WER、DER/JER、SA-WER/cpWER 和多说话人一致率。
- [ ] 数据库持久化：本轮按约束未迁移 SQLite；如需重启恢复任务，必须另行确认备份、迁移和回滚方案。

## 2026-08-23 二次复核修复

当前状态：基线实现已提交为 `f53acd2`；按用户授权继续修复，不处理 Docker，不联网下载模型，不安装依赖，不执行数据库迁移、部署或 Git push。

### 阶段 1：结果正确性与选项落地

- [x] 多模型校对按主段聚合全部重叠 reviewer 子段，不能只取最大重叠单段。
- [x] 加权共识支持文本相似度、分歧阈值、保留候选和不确定项处理策略，并保持用户选择的模型顺序。
- [x] 少于 20 个 speaker embedding 时仍遵守用户预设说话人数。
- [x] 时间戳级别、diarization 策略、全局 speaker 聚类、LLM 范围与模板等前端字段必须真实驱动后端；未实现的组合必须明确拒绝，不能静默忽略。
- [x] LLM 纪要与思维导图使用校对后的文本；文本、字幕和页面展示使用同一最终文本来源。

### 阶段 2：安全与远程模式

- [x] 模型未下载时默认明确报错，不静默联网；模型自带 requirements 默认不自动安装。
- [x] 远程 API 模式通过产物下载端点取文件，不读取后端绝对路径；公开任务状态不泄露服务端路径。
- [x] 音字联动内联 JSON 安全转义 `</script>`、`<`、`>`、`&` 和 Unicode 行分隔符。
- [x] 工作流错误信息与事件默认脱敏路径、Token、Header 和内部堆栈。

### 阶段 3：状态、资源与 CI

- [x] `events.jsonl` 包含导出成功和任务完成事件，与状态接口最终事件一致。
- [x] 避免同一 Paraformer 因校对和 diarization 重复加载；并行 reviewer 结果顺序确定。
- [x] 事件增量读取不复制完整历史；终态任务按 TTL 清理，避免进程内存无限增长。
- [x] 状态窗口补齐暂停自动滚动、级别筛选和日志下载能力。
- [x] CI 最小依赖与测试导入一致，干净环境不会因 torch/scipy 缺失直接失败。

### 可验证完成标准

- [x] 每个修复先有失败测试或可重复验证样例，阶段测试通过后再进入下一阶段。
- [x] 全量 pytest、compileall、前端/后端构建检查全部通过。
- [x] 复跑 60 秒真实音频工作流，核验多模型候选、speaker、最终文本、完整事件和下载产物。
- [x] 文档、`todo.md` 与 `chat_history.md` 使用 UTF-8 BOM + CRLF，无替换字符；`pytest.ini` 保持 pytest 可读取格式。

## 2026-08-23 前端栏目合并补完

纠正说明：此前“实施完成记录”只完成了后端复用、精细转录工作流和状态中心，没有完成规划文档要求的顶层导航收敛；本节以最终可见界面和浏览器验收为准，不再用后端阶段完成替代前端验收。

### 实施项

- [x] 顶层栏目从 8 个收敛为 4 个：转录工作台、实时识别、媒体与文本工具、模型与服务。
- [x] 转录工作台内合并快速转录、会议精细转录、说话人时间轴，复用现有接口和组件，不复制业务实现。
- [x] 媒体与文本工具内合并音频处理、跨语言翻译、情感识别，保留现有能力和事件绑定。
- [x] 服务与调试更名为模型与服务，并保持服务概览、模型管理、运行资源、任务队列、诊断与日志五区。
- [x] 补充导航结构测试，校验顶层栏目数量、名称和子栏目归属，防止再次只改文档未改界面。
- [x] 运行全量 pytest、compileall 和文本格式检查。
- [x] 重启本地 API/UI，浏览器逐项验证 4 个顶层栏目、子栏目和服务状态。
- [x] 更新规划文档、changelog、todo 和 chat_history，明确完成项及保留边界。

### 保留边界

- [x] Docker 继续忽略，不删除镜像或修改 Docker 文件。
- [x] 多说话人标注质量基线按用户要求暂缓，不作为本次导航与功能整合的阻塞项。
- [x] SQLite 持久化迁移仍需备份、迁移和回滚方案的单独确认，本次不执行。

## 2026-08-23 实时识别切换卡死修复

当前状态：用户在实际页面点击“实时识别”后卡死；上一轮仅验证页面可见，未覆盖用户现有页面中的重复切入/切出与首次组件挂载耗时。

- [x] 保留现场并检查 UI/API 健康、进程资源、运行日志和浏览器错误；API/UI 仍响应，但用户原页签已显示 `This page crashed`。
- [x] 定位为 10 个顶层/子级 Tab 强制 `render_children=True`，导致精细转录、媒体工具、模型服务和实时 Mic 等大型组件在同一页面预渲染，点击实时识别时触发浏览器崩溃风险。
- [x] 先把导航回归测试改为要求所有栏目按需渲染并确认旧实现失败，再做最小修复；未改流式接口协议。
- [x] 原崩溃页签不可恢复；后续改用独立干净页完成实际切换与录音链路验收，不再把旧 renderer 作为测试页面。
- [x] 相关前端测试 `47 passed`、全量测试 `238 passed, 3 subtests passed`、compileall 和运行中 `/config` 校验通过；10 个目标 Tab 的 `render_children` 均为 `false`。
- [x] 更新规划文档、changelog、todo 和 chat_history；CUDA 托管服务保持运行供用户测试。

### 第二次复测纠正

- [x] 用户反馈按需渲染后仍卡死；现场中 API/UI 服务仍健康，用户原页签仍处于 `This page crashed`。
- [x] 从运行中 Gradio 配置确认“实时识别”仍绑定一个后端 `select` 事件，且为 `queue=true`、`show_progress=full`；该事件仅关闭服务页自动刷新，却把导航错误地放入任务队列。
- [x] 先增加“实时识别不得存在 select 依赖”的回归断言并确认旧实现失败，再移除该后端回调。
- [x] 重启 CUDA 托管服务；运行中配置确认 `STREAM_SELECT_DEPENDENCIES=0`，UI/API 正常且设备为 CUDA。
- [x] 相关测试 `47 passed`、全量测试 `238 passed, 3 subtests passed`、compileall 通过。
- [x] 使用独立干净页完成重复切入/切出验收；旧 `This page crashed` renderer 保留为崩溃现场，不再用于验证新代码。

### 第三次复测与实际浏览器验收

- [x] 按用户要求在独立干净页面实际点击“实时识别”；旧布局可进入，但测试浏览器无麦克风，用户有真实音频设备，剩余差异锁定为 Mic 首次挂载和设备桥接。
- [x] 先增加失败测试，要求实时识别包含“文件流式识别 / Mic 实时识别”两个按需子页，文件子页不得包含任何麦克风来源，页面不得安装全局麦克风设备桥接。
- [x] 文件流式默认子页与 Mic 子页分开挂载；进入“实时识别”时不再创建 Mic 组件，只有用户显式选择 Mic 子页才初始化浏览器音频设备。
- [x] 删除全局 `getUserMedia` 覆盖、全页 `MutationObserver` 和页面加载时的设备枚举。
- [x] 修复隐藏“音频预览”默认同时声明 upload/microphone 的问题，改为只读、仅 upload 来源。
- [x] 最新代码重启后完成实际浏览器验收：首次进入实时识别仅挂载文件子页且无设备选择器；Mic 子页可独立挂载录制控件；文件/Mic/转录工作台/实时识别组合连续切换 10 轮，页面保持存活。
- [x] 相关测试 `47 passed`、全量测试 `238 passed, 3 subtests passed`、compileall、运行配置和文本格式检查通过。

### 第四次复测与系统麦克风落地

- [x] 用户继续反馈实际页签完全不可用；浏览器现场确认用户页签仍是 `This page crashed`，而独立测试页保持存活，差异集中在用户环境的浏览器音频 renderer。
- [x] 复用项目已有 PyAudio 后台采集、PCM16/16k 转换和 streaming 接口，不新增识别协议或重复 ASR 实现。
- [x] Mic 子页移除 `gr.Audio(sources=["microphone"])`，改为系统输入设备下拉、刷新设备、开始/停止按钮、状态、信号和转录输出；所有控制与轮询回调均 `queue=False`。
- [x] 本机确认 PyAudio 可用并枚举 18 个输入设备；系统默认“立体声混音”可启动但首帧较慢，显式选择 `2 - 麦克风 (ToDesk Virtual Audio)` 可稳定采集。
- [x] 后台真实采集验证：成功发送 2 个流式分片并正常停止；前端真实点击验证：发送分片从 2 增长到 8，停止后显示“录制已停止”，页面保持存活。
- [x] 实录完成后顶层栏目连续切换 10 轮，页面仍存活；相关测试 `47 passed`、全量测试 `238 passed, 3 subtests passed`、compileall 通过。
