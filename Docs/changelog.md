#
变更记录（Changelog）

## 2026-09-03（下午）

### 全量 pytest 验证通过 + ffmpeg 分块 fallback

- **全量 pytest 238 passed, 0 failed, 3 subtests passed**（46.5s）
- FakeModel 测试在 `chunk_enabled` 默认 True 后失败：ffmpeg 无法处理假音频 `b"fake-audio"` → 分块逻辑改为 try/except，失败时**自动 fallback 到单块**（原 chunks 列表），打 warning 日志
- 修复位置：`_workflow_transcribe_model` 分块段，生产环境 ffmpeg 异常也不会中断任务
- commit 6bde4f1

### 代码复用：ASR 分块 + NLLB 翻译分块下沉到公共层

- **离线识别 `/v1/audio/transcriptions` 自动分块**：duration > 5 分钟时自动 ffmpeg 240s/块 + 10s 重叠，复用 `_split_audio_ffmpeg` + `_merge_chunk_segments`，API 无额外参数，短音频走原路径。之前只有 workflow 路径分块，用户从"快速转录"tab 上传 2h 录音仍会截断到几千字
- **NLLB translate 内部分块**（4f63533）：下沉到 `NLLBTranslationModel.translate()` 方法内部，≤500 字/块按。！？!?\. 和换行切分。**所有路径自动受益**——工作流翻译、独立翻译 Tab、API `/v1/funasr/translate` 三入口统一；`_workflow_translate` 简化为 12 行（之前 45 行重复分块代码）
- README 新增"代码复用策略"章节，明确能力下沉位置与覆盖路径矩阵

### 长音频 ASR 默认启用分块（完整度 +1200%）
- `segmentation.chunk_enabled` 默认 False → True，后端 schema、前端配置、UI Checkbox 三层同步
- 不分块时 2h43m 录音仅识别 4262 字（截断）；分块后 56936 字，完整度提升 **+1236%**
- ffmpeg 切 240s/块 × 10s 重叠，合并时使用"文本前 40 字指纹 + 2×重叠时间窗口"去重，避免误删远距离真重复（如"对对对""是的"口语）
- 短音频（<chunk_seconds）自动降为单块，无性能损失

### LLM proofread scope=refined 提速 5-6×
- 默认校对范围从逐段 `segments`（HTTP 2659 次 / 45 分钟）改为全文拼接 `refined`（HTTP 9 次 / 7-8 分钟），提速 **5-6×**
- 新增 `_redistribute_refined_to_segments`：校对结果按原 segments 字符长度比例**精确回填**，保证 SRT/TXT 导出与全文一致（实测 40536 字精确匹配）
- 前端 UI 新增"全文拼接（推荐，快 10×）refined"选项并设为默认；逐段保留并标注"慢"

### 翻译长文本卡死修复（NLLB max_length=512）
- 工作流翻译阶段将全文一次性传入 NLLB，因 `max_length=512` 导致超长文本卡死 15+ 分钟并截断
- 新增分块逻辑：按句号/感叹号/问号切分，≤500 字/块逐块翻译后用换行拼接；70 分钟录音 28531 字翻译正常完成（40948 字英文）
- 单块短文本直接走原路径，无额外开销

### 精细转录全流程真实端到端验证通过
- 测试：IBEC竞标会议录音.m4a（32.4 MB / 70 分钟），启用双模型(sensevoice+paraformer)+校对+纪要+脑图+翻译+说话人分离
- 61 个事件全部 `_success`，无一失败；总耗时 29.6 分钟
- 结果：802 segments × 28531 字；校对正常；纪要 8 段聚合(JSON→summary.md)；脑图 26 节点(title+children)；翻译 40948 字完整英文；ZIP 9 文件齐全导出

### 其他稳定性与代码质量
- 去重窗口 `max(overlap_seconds*2, 30)` 改为严格 `overlap_seconds*2`，防止误删远距离重复
- 业务导航 Tab 移除后端 `select` 回调，新增单元测试确保不绑定
- 业务导航 Tab 子页 `transcription_tab/fine_tab/diarization_tab/...` 非服务 Tab 不得绑定 select，防止切换进入请求队列

## 2026-08-24

- 修复进入“实时识别”后切换栏目可能卡死的问题：删除 6 个非服务业务子 Tab 的后端 `select` 回调，避免普通导航进入 Gradio 请求队列。
- 新增导航回归断言，确保转录、实时识别、媒体工具及其子栏目均不绑定后端 `select` 依赖；模型与服务子页的轻量状态事件保持 `queue=False`。
- 重启 CUDA 托管 API/UI 并完成浏览器逐项验证；实时识别切换至转录工作台、媒体与文本工具、模型与服务均正常，页面无浏览器错误；全量测试 `234 tests OK`，compileall 通过。

## 2026-08-23

- “模型与服务”改为与其他栏目一致的两层菜单，内部包含服务总览、模型管理、运行资源、任务队列、诊断与日志五个按需子页；父级不再触发排队刷新，原接口和组件继续复用。
- 修复点击“实时识别”导致浏览器页签崩溃：10 个栏目按需挂载并移除实时 Tab 排队事件；文件流式与 Mic 分为独立子页，删除全局 `getUserMedia`/DOM 注入，最终以已有 PyAudio 系统采集替代浏览器麦克风组件。前端实录成功发送 8 个分片并正常停止，随后页面切换 10 轮保持存活，流式接口协议不变。
- 顶层导航从 8 个栏目收敛为 4 个：转录工作台、实时识别、媒体与文本工具、模型与服务；原能力归入 6 个子栏目并继续复用同一组件、回调和后端接口。
- 修复嵌套父 Tab 绑定后端选择事件导致首次切换卡住，以及音频处理重复输出组件、默认暴露麦克风来源和结果播放器可编辑的问题。
- 增加导航层级、子栏目归属、预渲染、音频来源和依赖输出唯一性测试；浏览器逐项切换验收通过，全量测试更新为 `238 passed, 3 subtests passed`。
- 整合精细转录工作流、模型目录、上传、任务事件、说话人时间轴、多模型校对和统一产物服务。
- 精细转录前端开放主/校对模型、流程、失败策略、时间戳、speaker、LLM、翻译、情感和导出选择，并增加可暂停、筛选、复制和下载的实时状态面板。
- 修复 reviewer 子段只取一段、短音频忽略预设说话人数、并行结果乱序、前端选项未生效和 LLM/导出文本不一致。
- 模型加载默认强制本地缓存，禁止静默下载和模型 requirements 自动安装；修复远程产物下载、服务端路径泄露、内联 JSON 注入和错误信息泄露。
- 终态事件产物包含“导出完成/任务完成”，任务事件增量读取并按 TTL 清理；复用 Paraformer 校对与 speaker 推理，避免重复加载。
- 非 Docker 全量测试 `237 passed, 3 subtests passed`；60 秒真实离线工作流复测通过。

## 2026-06-13

- 修复 Qwen3-ASR 离线字幕时间轴：保留强制对齐器的结构化字词时间戳，并按模型原生标点聚合真实句级边界
- 保留原有 `timestamp` 毫秒字段兼容性；结构化字词匹配失败时继续使用原有分段兜底
- 修复跨语言翻译三个 bug：Python 3.13 作用域问题（`import gradio as gr` 移到 `try` 块前）、`translate_tsv` 支持无表头 TSV、`convert_to_chinese_punctuation` 正则不再吃掉换行符
- `translate_srt`/`translate_tsv`/`translate_json` 增加 API 返回单字符串的兜底处理
- 新增 `aipython/tsv_to_srt.py` 转换工具

## 2026-06-08

- 初始化 "Docs/" 目录，补充项目分析文档：requirements/design/api/deployment/optimization-plan/tasks
- 新增输出模板/字幕升级策划文档："upgrade-plan-output-template.md"
- 新增模型能力矩阵与 API 参数差异文档："model-capability-matrix.md"
- API 支持多格式输出（txt/srt/vtt/tsv/all-zip）与 VAD 预设参数（vad_preset/merge_vad/merge_length_s），并更新 "Docs/api.md"
- 修正模型别名与官方文档口径：恢复 "fun-asr-nano"→"FunAudioLLM/Fun-ASR-Nano-2512"，新增 "qwen3-asr"→"Qwen/Qwen3-ASR-1.7B"，并保留 "qwen3-asr-0.6b"（更轻量可选）；同步补齐 Qwen3-ASR 所需依赖（qwen-asr）
- 增加分段/时间戳兜底逻辑：缺少 sentence_info 时按标点/长度切分并分配时间戳
- 增加跑批脚本：遍历 "test\\" 下媒体文件，输出到 "test\\<模型名>\\" 并生成 "run.log"
- 文档同步更新："Docs/deployment.md"、"Docs/tasks.md"

## 2026-06-09

- 同步更新根目录 "README.md"：补充 Pat WebUI 相关文档导航与当前保留启动入口
- 更新 "Docs/README.md"：补充 README 文档索引，并明确 "model-capability-matrix.md" 负责语言覆盖与项目接入差异说明
- 更新 "Docs/model-capability-matrix.md"：移除笼统“多语”表述，改为对齐本地缓存官方 README 的明确语言口径；补充 Qwen3-ASR 当前仅接离线路径说明
- 更新 "Docs/api.md"：修正 `trust_remote_code=False`、`dtype=fp16`、按需加载说明，以及各模型语言口径与接入限制说明
- 继续补全 "Docs/model-capability-matrix.md" 与 "Docs/api.md"：把 SenseVoice、Paraformer、Fun-ASR-Nano、Qwen3-ASR 的语言/中文方言/地域口音支持改为具体名单，并补上 `paraformer-zh-streaming` 与 `emotion2vec-plus-large` 的口径说明
- 清理根目录误生成的临时文件，并在 ".gitignore" 中加入 `trae-debug-log-*.txt`，避免调试日志再次污染工作区
- 更新 "Docs/smoke_pat_webui.md"：把旧的 `ready / not ready` 校验改成当前页面实际文案 `已加载 / 按需加载`
- 收口文档入口：在根目录 "README.md" 与 "Docs/README.md" 中补充 "smoke_pat_webui.md"、"changelog.md" 等导航项
- 重构 "FunASR_pat.bat" 启动链路：改由 "aipython/managed_single_window_launcher.py" 托管 API/UI 子进程，关闭启动窗口时自动结束子进程
- 更新 "Docs/deployment.md" 与根目录 "README.md"：补充新的单窗口托管启动行为与日志说明

## 2026-06-11

- 重写 Pat WebUI “流式识别”页 Mic 区域：正式页回到 Gradio 原生 `Audio(sources=["microphone"], streaming=True)`，不再自绘设备下拉、不注入 `getUserMedia`、不跳转 `/mic-stream`
- 文件流式识别与 Mic 实时识别保持左右两栏；Mic 状态、信号、输出、下载与文件流式输出互相独立
- 对照已知可用提交 `4496919` 恢复 Mic 原生生命周期：组件首次渲染即参与权限与设备枚举，不再通过回调切换 `interactive` 或重建 Audio 组件
- 恢复 `start_recording` 初始化会话并检查模型 ready 的原有流程，移除额外的“加载流式模型”按钮
- 修复 Gradio numpy int16 双声道音频转换顺序，避免双声道均值后被误判为 float 并放大削波
- 流式输出预览改为单段连续显示，避免短句被自动拆成多行；下载文本继续使用 UTF-8 BOM
- 修复 FunASR `sentence_info` 毫秒时间戳转换错误，`1500ms` 现在正确输出为 `1.5s`
- 新增独立 Gradio Mic 流式识别测试页："aipython/gradio_streaming_asr_test.py" 与 "run_gradio_streaming_asr_test.bat"，用于隔离验证 Gradio `Audio.stream` 链路

## 2026-06-12

### Mic 流式收音修复

- 修复流式 Mic 收音问题：恢复 `gr.Audio(sources=["microphone"], streaming=True)` 替代 `gr.Microphone`
- `stream_transcribe_microphone` 从 `yield` 生成器改为同步 `return`，避免被 Gradio 注册为生成式输出事件
- 增加隐藏设备桥接 JS（`MIC_DEVICE_BRIDGE_JS`），拦截 `getUserMedia` 注入设备 ID，修复 Gradio 原生设备下拉框不生效问题
- 关闭浏览器 AEC、降噪和自动增益，固定单声道输入，增加音频轨道 mute/unmute/ended 诊断日志

### 信号显示优化

- 合并「麦克风识别状态」和「麦克风信号」为单个 Textbox（2 行显示）
- 峰值/RMS 从原始小数改为百分比显示（如 `0.0625` → `6.2%`）
- 添加 ASCII 音量条 `[████░░░░]` 直观显示信号强度
- 添加 CSS 放大麦克风波形显示幅度（scaleY: 3）
- 降低静音判断阈值：peak 1% → 0.1%，rms 0.3% → 0.03%

### 模型下载状态

- 服务与调试页面模型列表「当前状态」改为「本地状态」，显示模型是否已下载至本地
- 下拉列表不再显示下载状态，保持简洁
- 检测逻辑：检查 `workspace/models/models` 目录下的实际模型文件（config.yaml、model.pt 等）
- 支持 FunASR 别名映射（paraformer-zh → iic/speech_paraformer-...）和 Qwen 路径格式（点号替换为三个下划线）

### NLLB 翻译能力

- 补充 NLLB 模型（nllb-200-distilled-600m、nllb-200-distilled-1.3b）的 translation 能力标识
- 说明：「多语种文本翻译；支持 200+ 语言互译」
- 能力筛选器新增「文本翻译」选项

### UI 布局调整

- 移除各 tab 的静态兜底模型列表提示和流式识别页 Paraformer Streaming 提示文本
- 离线识别页面「单文件处理」和「批量文件处理」改为左右分栏布局（类似流式识别页面）
- 批量文件处理的按钮移到上传组件下方
