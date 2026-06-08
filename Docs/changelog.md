﻿#
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
