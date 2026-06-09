# Debug Session: sensevoice-emotion-failure [OPEN]

## 程序说明

本文件用于记录 `sensevoice` 情感识别失败问题的运行时调试过程、假设、证据、修复与验证结果。

## 用户现象

- `sensevoice` 情感识别失败

## 可证伪假设

1. 当前运行中的 `8000` 端口仍是旧版后端进程，未加载当前源码里的情感识别实现
2. 前端把 `sensevoice` 发到了错误接口或传了不兼容参数，导致后端返回失败
3. 后端 `sensevoice` 情感识别路径对返回结构解析过于乐观，当前模型返回格式与代码预期不一致
4. `sensevoice` 当前在情感识别路径里复用了不适合该任务的模型加载参数，导致运行时报错
5. 失败并非 `sensevoice` 专属，而是情感识别接口整体不可用

## 当前计划

- 检查当前 `emotion` 接口实现与运行中的服务状态
- 用真实请求复现 `sensevoice` 情感识别失败
- 基于日志和返回体判断命中哪条假设
- 必要时做最小修复并验证

## 运行证据

### Pre-fix

- 运行中的 `8000` 接口可复现：
  - `POST /v1/funasr/emotion`
  - `model=sensevoice`
  - `granularity=utterance`
  - 返回 `200`
  - 但 `top_emotion=""`、`emotions=[]`
- 同一音视频样本 `test/1.mp4` 的真实模型原始返回中，`text` 实际包含情感 token，例如：
  - `< | HAPPY | >`
  - `< | ANGRY | >`
  - `< | NEUTRAL | >`
  - `< | EMO _ UNKNOWN | >`
- 现有后端实现只匹配紧凑格式：
  - `<|HAPPY|>`
- 因为真实返回带空格，导致 token 全部漏匹配，所以虽然接口不报错，但情感结果恒为空

### 根因

- `sensevoice` 情感识别失败不是模型没返回情感
- 根因是 `"app/openai_api/server.py"` 中 `build_sensevoice_emotion_payload(...)` 的正则过窄，只支持紧凑 token，不支持真实返回中的带空格 token 格式

## 修复

- 放宽 `build_sensevoice_emotion_payload(...)` 的 token 提取逻辑：
  - 兼容 `<|HAPPY|>`
  - 兼容 `< | HAPPY | >`
  - 兼容 `EMO _ UNKNOWN` 这类带空格下划线形式
- 归一化时去掉 token 内部空白后再判断情感标签
- 仅将 `HAPPY / SAD / ANGRY / NEUTRAL` 计入情感结果，忽略 `EMO_UNKNOWN` 等非目标标签

## 回归测试

- 已新增 `"tests/test_server_emotion_endpoint.py"` 回归用例：
  - `test_emotion_supports_sensevoice_spaced_tokens`
- 已通过：
  - `python -m unittest "tests.test_server_emotion_endpoint" -v`
  - `python -m py_compile "app/openai_api/server.py"`

## 运行态验证

- 用与 `"run_api.bat"` 一致的运行环境在临时 `8001` 启动当前源码后，真实请求返回：
  - `STATUS=200`
  - `top_emotion="happy"`
  - `emotions=[{"label":"happy","score":1.0}]`
- 随后已重启正式 `8000` 为当前修复后的代码
- 对正式 `8000` 再次真实请求验证通过，返回同样为：
  - `STATUS=200`
  - `top_emotion="happy"`

## 假设结论

1. 当前运行中的 `8000` 端口仍是旧版后端进程，未加载当前源码里的情感识别实现
   - 结论：部分成立。最初 `8000` 确实还是旧进程，修复后已重启为新代码
2. 前端把 `sensevoice` 发到了错误接口或传了不兼容参数，导致后端返回失败
   - 结论：否
3. 后端 `sensevoice` 情感识别路径对返回结构解析过于乐观，当前模型返回格式与代码预期不一致
   - 结论：是
4. `sensevoice` 当前在情感识别路径里复用了不适合该任务的模型加载参数，导致运行时报错
   - 结论：否
5. 失败并非 `sensevoice` 专属，而是情感识别接口整体不可用
   - 结论：否

## 当前状态

- 修复已完成
- 正式 `8000` 已切换到修复后的代码
- 临时调试 `8001` 已停止
