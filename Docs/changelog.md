#
变更记录（Changelog）

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
