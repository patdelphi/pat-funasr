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
- 更新 "Docs/smoke_pat_webui.md"：把旧的 `ready / not ready` 校验改成当前页面实际文案 `已加载 / 按需加载`
- 收口文档入口：在根目录 "README.md" 与 "Docs/README.md" 中补充 "smoke_pat_webui.md"、"changelog.md" 等导航项
- 重构 "FunASR_pat.bat" 启动链路：改由 "aipython/managed_single_window_launcher.py" 托管 API/UI 子进程，关闭启动窗口时自动结束子进程
- 更新 "Docs/deployment.md" 与根目录 "README.md"：补充新的单窗口托管启动行为与日志说明
