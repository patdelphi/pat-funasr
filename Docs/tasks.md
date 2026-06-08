#
待办事项（Tasks）

## 高优先级

- 修复 device fallback 不一致：让 "FunASR.bat" 在无 CUDA 时不要启动 "--device cuda" 的 "run_api.bat"
- 处理 "下载模型.bat" 引用缺失脚本（"scripts/download_model.py"）的问题：补齐或移除入口并更新文案
- 优化 "停止服务.bat" 的停止策略，避免按内存阈值误杀其他 Python 进程

## 中优先级

- 将 API 的 "batch_size"、"timeout"、"VAD 分段参数" 做成可配置项（命令行或环境变量）
- 为 GPU 场景增加请求队列/限流，降低并发导致 OOM 的概率
- 增加 warm-up 机制，降低首包延迟与启动抖动

## 低优先级

- 运行时体积与依赖清单整理（便携包裁剪、依赖锁定策略）

