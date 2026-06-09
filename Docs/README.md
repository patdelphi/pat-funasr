#
Pat WebUI 项目分析文档


说明：当前执行清单统一使用根目录的 "todo.md"；本目录主要存放分析、设计、接口与规划文档，不再作为日常开发的任务入口。

## 外部参考

- 官方教程：[FunASR 使用教程](https://modelscope.github.io/FunASR/zh/tutorial.html)
- 官方 API：[FunASR API 文档](https://modelscope.github.io/FunASR/api.html)
- 官方仓库：https://github.com/modelscope/FunASR

## 文档索引

- 根目录 "README.md"：仓库入口与重点文档导航
- "requirements.md"：运行环境与依赖约束（OS/GPU/Python/端口/环境变量）
- "design.md"：整体架构与关键数据流（启动链路、模型加载与请求处理）
- "api.md"：OpenAI 兼容 API 说明（端点、参数、响应、错误约定）
- "deployment.md"：本地运行与发布建议（Windows 便携包形态、脚本说明、排错要点）
- "optimization-plan.md"：性能与稳定性优化计划（GPU/并发/缓存/可观测性）
- "upgrade-plan-output-template.md"：输出模板/字幕升级策划（对标 Whisper-CTranslate2 参数体系）
- "model-capability-matrix.md"：模型能力矩阵、语言覆盖、项目接入差异与 API 参数说明
- "smoke_pat_webui.md"：Pat WebUI 手工冒烟清单，覆盖服务页摘要、按需加载文案与主流程验证
- "tasks.md"：历史任务草稿（保留参考，不作为当前执行入口）
- "changelog.md"：分析文档变更记录

## 代码入口速览

- 启动器（推荐）：["FunASR_pat.bat"](../FunASR_pat.bat)
- API 启动：["run_api.bat"](../run_api.bat)
- Pat WebUI：["run_ui_pat.bat"](../run_ui_pat.bat)
- API 服务：["app/openai_api/server.py"](../app/openai_api/server.py)
- UI：["app/pat_funasr_webui/gradio_app.py"](../app/pat_funasr_webui/gradio_app.py)

## 测试（test 目录跑批）

用途：遍历 "test\\" 下音视频文件，分别用各模型生成 ASR 输出（txt/srt/vtt/tsv/json/zip）。

- 脚本：["run_test_all_models.ps1"](../run_test_all_models.ps1)
- 默认模型：sensevoice / paraformer / fun-asr-nano（不包含 paraformer-en）
