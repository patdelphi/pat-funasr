#
变更记录（Changelog）

## 2026-06-13

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
