# 优化执行 TODO（2026-09-16）

依据：Docs/code-review-20260916-v2.md + Docs/optimization-plan-20260916.md

## 阶段 1：高优先级 Bug 修复
- [x] 1a. server.py 删除分块手动偏移（#1）
- [x] 1b. server.py NLLB tokenizer 并发防护（#6）
- [x] 1c. server.py 流式 per-session 锁（#7）
- [x] 1d. gradio_app.py 定义 _simple_status（#2）
- [x] 1e. workflow_service.py 空 asr_model 跳过校验（#3）
- [x] 1f. audio_sync_js.py fallback 转义（#4）
- [x] 1g. start_services.py 修正指向或删除（#5）——已确认删除
- [x] 1h. 阶段 1 回归测试（含新增单测）全量跑 pytest —— 245 passed

## 阶段 2：中优先级健壮性
- [x] 2a. server.py overlap/chunk 校验、死参数、renderers CRLF、.env 去引号
- [x] 2b. 边界防护（chunk_text、translation_utils）
- [x] 2c. 重复调用去重、麦克风懒加载
- [x] 2d. model_catalog 注释/死映射修正
- [x] 2e. 脚本修复（batch_transcribe、ps1、bat）
- [x] 2f. .gitignore 补 .env.local.bat
- [x] 2g. 阶段 2 回归测试 —— 245 passed

## 阶段 3：测试补强
- [x] 3a. 熔断恢复/fallback 链测试
- [x] 3b. 分块合并边界测试
- [x] 3c. 异常音频路径测试
- [x] 3d. 测试自身修复（硬编码路径、_TEST_TS、轮询等待）
- [x] 3e. 全量回归 —— 257 passed

## 阶段 4：清理（需确认）
- [x] 4a. 调试埋点移除（_dbg_report/is_debug_report_enabled/_dbg_browser_instrumentation 等）
- [x] 4b. 未绑定组件死代码清理（6 个无引用定义，被测试引用的保留）
- [x] 4c. 重复工具抽取（评估后不执行：跨模块抽取收益低，保持最小改动）
- [x] 4d. aipython 归档（16 个无引用诊断脚本移入 aipython/archive/，测试引用的 5 个留原地）
- [x] 4e. 全量回归 —— 254 passed（较 257 减少 3 个随埋点删除的测试）

## 阶段 5：功能新增（用户已确认执行，跳过逐项选择，按 8 项全做）
- [x] 5a. 任务队列持久化（SQLite，重启保留未完成任务）
- [x] 5b. 熔断状态可视化（WebUI 展示 llm_client._fuse_state）
- [x] 5c. 按说话人导出音频切片（基于 diarization 结果）
- [x] 5d. DOCX/CSV 导出（扩展 ZIP 产物格式）
- [x] 5e. 热词热更新 API（运行时更新，免重启）
- [x] 5f. CI 覆盖率门禁（GitHub Actions，核心模块 ≥65%）
- [x] 5g. SSE 事件推送（workflow 进度替代轮询，新增 /events/stream）
- [x] 5h. 模型显存释放（闲置模型释放 GPU 显存，TTL 可配）
- [x] 5i. 全量回归 —— 278 passed（含 5a/5g/5h 新增 11 个测试；覆盖率门禁 66.83% ≥ 65%）
