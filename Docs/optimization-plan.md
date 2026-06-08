#
性能与稳定性优化计划（Optimization Plan）

## 性能（推理吞吐/延迟）

- GPU 预热与首包延迟
  - 现状："start_services.py" 预加载模型，能减少首个请求的抖动
  - 建议：在服务启动后做一次短音频的空跑（warm-up），把 CUDA context、kernel、cache 都预热
- 批处理与分段策略
  - 现状：API 固定 "batch_size=1"
  - 建议：引入可配置 batch_size；对长音频配合 VAD 分段合批，提高吞吐
- 并发与队列
  - 现状：FastAPI + Uvicorn 单进程模式（默认）
  - 建议：为 GPU 模型增加请求队列/限流，避免并发过高导致 OOM；对 CPU 场景可考虑多进程 workers

## 稳定性（健壮性/可维护性）

- 设备选择一致性
  - 现状："FunASR.bat" 有 “fallback to CPU” 文案，但 "run_api.bat" 固定 "--device cuda"
  - 建议：统一 device 决策入口，让 BAT 把 device 传给 API（或直接统一改用 "start_services.py" 启动）
- 模型下载脚本一致性
  - 现状："下载模型.bat" 引用的 "scripts/download_model.py" 缺失
  - 建议：补齐脚本或删除该入口，改为明确说明“首次启动自动下载”
- 进程停止策略
  - 现状："停止服务.bat" 会按内存阈值杀 "python.exe"，可能误伤
  - 建议：改为基于 PID 文件/端口占用/窗口标题的精确停止；或让 "start_services.py" 负责生命周期

## 可观测性（定位问题更快）

- 结构化日志
  - 现状：API 使用 logging，错误会返回 HTTP 500，并写日志
  - 建议：补充请求耗时、音频时长、模型名、device、CUDA 显存等关键字段（避免记录音频内容）
- 资源指标
  - 建议：增加一个只读端点返回 GPU 显存占用、当前队列长度、已加载模型信息，便于运维排障

