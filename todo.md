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

## 阶段 6：TranslateGemma 集成（2026-09-17，用户确认：新增可选模型 + 本地优先/联网下载 + 优先 GPU）
- [x] 6a. model_catalog.py：MODEL_CONFIGS 与 MODEL_CAPABILITIES 新增 translategemma-4b-it（type=translation）
- [x] 6b. server.py load_model translation 分支按模型分派：NLLB 走现有 Seq2Seq 逻辑；TranslateGemma 走 AutoProcessor + AutoModelForImageTextToText + apply_chat_template；新增 FLORES→ISO639 映射；本地缓存存在禁止联网、否则允许联网下载；GPU 优先、CUDA 失败回退 CPU
- [x] 6c. /v1/translations 适配 TranslateGemma：num_beams 忽略、max_length 转 max_new_tokens（模型类内处理，端点零改动）
- [x] 6d. gradio_app.py：跨语言翻译 tab 与精细转录 tab 模型下拉增加 "TranslateGemma-4B-IT"
- [x] 6e. 测试：新增 test_server_translategemma.py（7 个用例：注册/加载分支/联网下载/语言码映射/未知码报错/长文本分块/GPU 回退）
- [x] 6f. 全量回归 —— 286 passed（原 279 + 新增 7）；阶段 6 变更摘要 Docs/phase6-translategemma-20260917.md

## 阶段 6-GGUF：TranslateGemma GGUF 量化版（用户确认按 GGUF 方案执行）
- [x] 6g. 方案调研：google/translategemma-4b-it 为 gated（manual，需 HF token）；改用社区量化仓库 mradermacher/translategemma-4b-it-GGUF（免 token），选 Q4_K_M（2.6GB）
- [x] 6h. 环境：安装 llama-cpp-python 0.3.30（cu124 GPU wheel，RTX 3080；0.3.35 在 Ryzen 5800X 无 AVX-512 报 0xc000001d）；Q4_K_M 下载到 C 盘 HF 全局缓存（models--mradermacher--translategemma-4b-it-GGUF）
- [x] 6i. model_catalog.py：MODEL_CONFIGS 新增 translategemma-4b-it-gguf（format=gguf）；MODEL_CAPABILITIES 新增条目；_has_model_payload 兼容仅含 .gguf 无 config.json 的仓库（修复 HF 快照布局命中）
- [x] 6j. server.py：load_model translation 分支新增 format=gguf 分支（llama_cpp.Llama 加载、n_gpu_layers=-1 优先 GPU、失败回退 CPU；定位 .gguf 排除 mmproj；内嵌 TranslateGemmaGgufTranslationModel：FLORES→ISO 映射 + Gemma3 prompt + 1200 字分块）
- [x] 6k. gradio_app.py：精细转录 tab 与跨语言翻译 tab 翻译模型下拉增加 "TranslateGemma-4B-IT (GGUF)"
- [x] 6l. 测试：新增 test_server_translategemma_gguf.py（7 个用例：注册/加载走 Llama n_gpu_layers=-1 且选 Q4_K_M/prompt 构造/未知码/分块/GPU 回退 CPU/缺 .gguf 报错）；test_model_configs.py 补 HF 快照布局解析测试
- [x] 6m. 验证：真实加载冒烟测试通过（加载 2.7s GPU、翻译 0.5s、输出正确）；全量回归 —— 293 passed；阶段 6 变更摘要补充 GGUF 部分

## 阶段 6-显存：翻译模型显存优化（用户反馈 NLLB 600M 卡慢后确认 A+B）
- [x] 6n. 诊断：RTX 3080 10GB 显存 9870/10240 MiB 爆满——1.3B(fp32 5.1GB)+600M(fp32 2.3GB)+GGUF(2.6GB)+FunASR 同时驻留（idle TTL 30min），600M 推理几乎 OOM 导致极慢
- [x] 6o. A-NLLB fp16：server.py NLLB 加载分支 `.half().to(device)`（600M 省 1.15GB、1.3B 省 2.55GB）
- [x] 6p. B-切换自动卸载：server.py 新增 `_unload_all_models_except`（后扩展为含 FunASR 全部清空），load_model 翻译分支加载前卸载其他模型（GGUF 调 llama.close()、transformers/funasr 释放权重 + empty_cache）
- [x] 6q. 测试：test_server_translation_endpoint.py 新增 4 用例（fp16 加载/切换卸载/GGUF close/保留同模型）—— 相关 11 passed

## 阶段 6-显存2：翻译切换稳定性（用户报 500/502 后修复）
- [x] 6r. 诊断1（500）：本地全路径复现验证通过（主线程/子线程/TestClient/GGUF↔NLLB 交替均正常），根因为运行中 server 进程 CUDA 上下文瞬时状态；NLLB GPU 加载失败已回退 CPU（不再 500）
- [x] 6s. 诊断2（502）：server 进程崩溃（网关 502）——llama.cpp 显存不足时 C++ 层 abort() 直接杀死进程，Python 无法捕获；本地同序复现无法触发（显存充足）
- [x] 6t. 修复：GGUF 分支加载前检查显存余量（<4GB 回退 n_gpu_layers=0 CPU），防止 llama.cpp abort 崩溃；test_server_translategemma_gguf.py 新增显存不足回退用例 —— GGUF 8 passed
- [x] 6u. 并发保护（用户选 C）：切换翻译模型只卸载其他翻译模型（FunASR 保留，不打断并发转录）；GGUF 显存不足时先卸载 FunASR 腾显存再重试 GPU、仍不足才回退 CPU；测试覆盖两种分支 —— 相关 21 passed

## 阶段 6-适配：模型缓存与 UI（用户确认）
- [x] 6v. 查找顺序共享缓存优先（ModelScope→HF 全局缓存），项目 workspace/models 兜底；resolve 兼容 ms+hf 布局（_has_model_payload 兼容 GGUF）；GGUF 本地未命中自动 snapshot_download 到 HF 共享缓存后加载
- [x] 6w. UI：精细转录 tab 与跨语言翻译 tab 翻译模型下拉只保留 "TranslateGemma-4B-IT (GGUF)"（移除 NLLB 600M/1.3B 与非 GGUF gemma3）；NLLB/gemma3 注册与 API 保留，端点仍可用
- [x] 6x. 测试：GGUF 自动下载/下载失败/缺文件报错用例 + roots 顺序断言 —— 相关 21 passed；全量回归后台
