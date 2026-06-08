#
FunASR-Portable-GPU 项目分析文档

本目录用于存放对 "FunASR-Portable-GPU" 的项目结构、运行机制、API 以及部署/优化建议的分析文档。

## 文档索引

- "requirements.md"：运行环境与依赖约束（OS/GPU/Python/端口/环境变量）
- "design.md"：整体架构与关键数据流（启动链路、模型加载与请求处理）
- "api.md"：OpenAI 兼容 API 说明（端点、参数、响应、错误约定）
- "deployment.md"：本地运行与发布建议（Windows 便携包形态、脚本说明、排错要点）
- "optimization-plan.md"：性能与稳定性优化计划（GPU/并发/缓存/可观测性）
- "tasks.md"：待办事项清单（从代码/脚本中发现的改进点与风险项）
- "changelog.md"：分析文档变更记录

## 代码入口速览

- 启动器（推荐）：["start_services.py"](file:///y:/NewStore/AI/FunASR-Portable-GPU/start_services.py)
- 一键脚本：["FunASR.bat"](file:///y:/NewStore/AI/FunASR-Portable-GPU/FunASR.bat)
- API 服务：["app/openai_api/server.py"](file:///y:/NewStore/AI/FunASR-Portable-GPU/app/openai_api/server.py)
- UI：["app/openai_api/gradio_app.py"](file:///y:/NewStore/AI/FunASR-Portable-GPU/app/openai_api/gradio_app.py)

