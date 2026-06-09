# Debug Session: ui-not-accessible [OPEN]

## 程序说明

本文件用于记录 `"FunASR_pat.bat"` 启动后 API 正常但 WebUI 无法访问的问题的运行时调试过程、假设、证据、修复与验证结果。

## 用户现象

- 启动脚本显示：
  - `[1/2] starting API`
  - `[2/2] starting UI`
  - `launched: API=8000, Pat WebUI=7861/7862/7863`
- 日志中只看到 API 启动成功
- 无法访问 WebUI

## 可证伪假设

1. `"run_ui_pat.bat"` 根本没有被成功启动，`Start-Process` 的参数转义对 UI 这条链路失效
2. UI 进程启动后立即退出，错误输出没有正确写入 `"funasr-single-window.log"`
3. UI 实际启动了，但不是 `7861`，而是落到了 `7862/7863`，提示文案误导了访问地址
4. UI 成功监听了端口，但 `gradio_app.py` 内部启动后又因异常退出，当前日志还没完整捕获
5. `"run_ui_pat.bat"` 在隐藏启动时环境变量或工作目录不对，导致与直接运行表现不一致

## 当前计划

- 复现启动链路
- 检查 `7861/7862/7863` 监听情况与 UI 进程状态
- 读取 `"funasr-single-window.log"` 与批处理启动方式的真实证据
- 基于证据做最小修复并验证

## 运行证据

### Pre-fix

- 用户提供的启动日志只包含 API 成功启动信息
- 本地检查确认：
  - `8000` 监听成功
  - `7861/7862/7863` 全部未监听
  - 没有任何 `gradio_app.py` / `run_ui_pat.bat` 相关进程
- 直接单独运行 `"run_ui_pat.bat"` 时：
  - `7861` 正常监听
  - 说明 Gradio/UI 本身没有问题
- 复现 `"FunASR_pat.bat"` 启动链路时，先后抓到两类问题：
  1. `Start-Process` 不允许把 `RedirectStandardOutput` 和 `RedirectStandardError` 指向同一个文件
  2. API 与 UI 共用同一个日志文件时，第二个进程在 Windows 下会因文件占用无法正常重定向输出

### 根因

- 总启动脚本 `"FunASR_pat.bat"` 的 UI 隐藏启动链路不稳定
- 不是 `gradio_app.py` 本身起不来
- 真正问题在于：
  - UI 启动命令过度内联，参数转义复杂
  - API/UI 共用一个日志文件，Windows 下文件重定向发生冲突

## 修复

- `"FunASR_pat.bat"`
  - 不再让 API/UI 共用同一个 `"funasr-single-window.log"`
  - 改为分别写：
    - `"funasr-api.log"`
    - `"funasr-ui.log"`
  - UI 启动路径回退为已验证可用的 `"run_ui_pat.bat"`
  - 总脚本先选定 UI 端口，再通过环境变量 `FUNASR_UI_PORT` 传给 `"run_ui_pat.bat"`
  - 当前窗口同时 `tail` 两份日志
- `"run_ui_pat.bat"`
  - 新增支持读取 `FUNASR_UI_PORT`，有值时直接使用该端口；无值时仍按原逻辑自动选端口

## Post-fix

- 用干净环境重跑 `"FunASR_pat.bat"` 后：
  - `8000` 正常监听
  - `7861` 正常监听
  - `"funasr-ui.log"` 已写入：
    - `Starting Pat WebUI on http://127.0.0.1:7861`
  - 总启动窗口持续输出 API 日志，且 `/v1/models` 已收到来自 UI 的请求

## 假设结论

1. `"run_ui_pat.bat"` 根本没有被成功启动
   - 结论：部分成立。UI 隐藏启动链路有问题，但 `run_ui_pat.bat` 本身可单独正常工作
2. UI 进程启动后立即退出，错误输出没有正确写入日志
   - 结论：是
3. UI 实际启动了，但不是 `7861`
   - 结论：否
4. UI 成功监听了端口，但 `gradio_app.py` 内部启动后又因异常退出
   - 结论：否
5. `"run_ui_pat.bat"` 在隐藏启动时环境变量或工作目录不对
   - 结论：否，真正根因是隐藏启动命令和日志重定向冲突

## 当前状态

- 修复已完成并通过运行态验证
- 当前 `8000` 与 `7861` 均处于运行状态
- 调试会话保持 `[OPEN]`，等待用户确认是否需要清理本次调试文件与临时服务
