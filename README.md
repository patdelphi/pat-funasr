#
Pat WebUI

说明：本仓库是 FunASR 的 GPU 便携版封装（Windows），当前主入口已收敛到 Pat WebUI 方案。

项目分析与升级策划文档在：

- "Docs/README.md"

当前与 Pat WebUI / 模型能力直接相关的重点文档：

- "Docs/README.md"：项目文档总索引
- "Docs/model-capability-matrix.md"：模型能力矩阵、语言覆盖与当前项目接入差异
- "Docs/api.md"：后端 API、模型配置、加载方式与返回协议
- "Docs/smoke_pat_webui.md"：Pat WebUI 手工冒烟清单
- "Docs/changelog.md"：本轮文档与能力口径收敛记录

当前保留的启动入口：

- "FunASR_pat.bat"：单窗口托管启动 API + Pat WebUI；关闭启动窗口时会自动停止子进程
- "run_api.bat"：仅启动 API
- "run_ui_pat.bat"：仅启动 Pat WebUI
