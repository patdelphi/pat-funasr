#
Debug Session: gradio-page-hung

状态：[OPEN]

## 症状

- Pat WebUI 页面在执行一次很小的离线识别后卡死
- 浏览器报错：RESULT_CODE_HUNG
- 期望：页面可正常返回结果，不卡死；同时能看到模型 load / 推理相关日志

## 环境

- OS：Windows
- 项目目录："Y:\\NewStore\\AI\\FunASR-Portable-GPU"
- UI：Gradio（Pat WebUI）
- API："/v1/audio/transcriptions"

## 可复现步骤（待确认）

1. 运行 "FunASR_pat.bat" 或分别启动 API + Pat WebUI
2. 打开 Pat WebUI
3. 上传一个小文件并点击离线识别
4. 页面卡死，出现 RESULT_CODE_HUNG

## 可证伪假设（3-5）

1. Gradio 前端在接收某个输出组件（预览文本 / JSON / 日志）时触发超大渲染，导致浏览器渲染线程 hang。
2. Gradio 的 Timer（运行日志自动刷新）与“离线识别返回”的 UI 更新并发，导致前端事件队列堆积/阻塞。
3. 后端返回内容体积正常，但前端组件类型（如 Code 语法高亮）在特定内容上触发性能问题（例如 JSON 过深/过长行）。
4. 前端并非“渲染卡死”，而是后端请求未返回导致页面等待，但浏览器将其判定为 hang（需要比对 API 响应耗时、状态码与 UI 事件日志）。
5. 控制台/日志中缺少推理信息导致误判：实际推理过程发生但日志未打印（需补充开始/结束关键日志并对齐到一次请求）。

## 下一步（按证据驱动）

1. 启动 Debug Server，采集运行时事件（UI 侧输出长度、Timer tick 频率、请求耗时、返回状态）
2. 仅加“插桩上报”，不改业务逻辑，复现一次
3. 基于日志证据确认根因后，再做最小修复

