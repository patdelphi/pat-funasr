#
待办事项（Tasks）

## 高优先级

- ✅ 修复 device fallback 不一致：让 "FunASR.bat" 把探测到的 device 传给 "run_api.bat"
- ✅ 补齐 "下载模型.bat" 入口：新增 "scripts/download_model.py"
- ✅ 优化 "停止服务.bat" 的停止策略：不再按内存阈值误杀其他 Python 进程

## 已完成（本轮）

- ✅ 增强 ASR 输出：支持 txt/srt/vtt/tsv/all(zip)，并提供分段/时间戳兜底（字幕不再整段）
- ✅ 提供跑批脚本：使用 "run_test_all_models.ps1" 遍历 "test\\" 输入并输出到 "test\\<模型名>\\"

## 中优先级

- 将 API 的 "batch_size"、"timeout"、"VAD 分段参数" 做成可配置项（命令行或环境变量）
- 为 GPU 场景增加请求队列/限流，降低并发导致 OOM 的概率
- 增加 warm-up 机制，降低首包延迟与启动抖动

## 低优先级

- 运行时体积与依赖清单整理（便携包裁剪、依赖锁定策略）
