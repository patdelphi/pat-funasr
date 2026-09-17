# 代码审查报告 v2（2026-09-16，修正版）

> 审查范围：`app/`（业务代码约 1.5 万行，不含 `app/funasr` 第三方库）、`tests/`（33 个测试文件）、`scripts/`、`aipython/`、启动脚本与配置。
> 审查方式：4 个子代理分模块只读审查 + 主代理两轮逐行复核，并查阅 `app/funasr` 第三方库源码（qwen3_asr/model.py、auto/auto_model.py 等）与项目历史结论交叉验证。
> 本版为最终正确版；与 v1 的差异见文末「与 v1 差异」。
> 状态标记：⚠️ 高优先级（Bug/逻辑错误）｜🔶 中优先级（健壮性/冗余）｜🔹 低优先级（优化/风格）

## 一、高优先级（Bug / 逻辑错误，均经复核确认）

### ⚠️ 1. 离线 API 分块模式时间戳双重偏移
- 位置：`app/openai_api/server.py:1375-1381` + `app/pat_funasr_webui/fine_transcription/transcription_pipeline.py:144-159`
- 证据：`_run_asr` 分块分支先在 1375-1377 行手动给每块 segments 加 `offset`，随后 1381 行把 `all_segs` 与 `offsets` 传给 `_merge_chunk_segments`，而该函数内部（pipeline:144-146 segments、148-159 words）再次加偏移；其 docstring（pipeline:126-130）明确要求传入**未加偏移**的 segments。
- 影响范围（已精确化）：`_run_asr` 仅被 `/v1/audio/transcriptions` 调用（server.py:2066-2077）。触发条件为 `chunk_enabled=true`（显式传参）或音频 >90min（sensevoice/paraformer 等自动开启）。此时第 2+ 块 SRT/VTT/segment 时间戳按倍数错乱（第 3 块约 +480s 而非 +240s）。**文本合并不受影响**（只做指纹去重）；workflow 路径（`_workflow_transcribe_model`，server.py:1513 起）有自己的分块循环、不预加偏移，**正确**。
- 修复：删除 `_run_asr` 中 1375-1377 的手动偏移循环。
- 测试缺口：`_run_asr` 分块主链路时间戳无测试，致漏检。

### ⚠️ 2. WebUI 上传音频回调 NameError
- 位置：`app/pat_funasr_webui/gradio_app.py:4738/4744/4755`
- 证据：`stream_media_file` / `emotion_media_file` / `diarization_media_file` 三个上传回调引用 `_simple_status`，全项目仅 3 处调用、**无定义**（已 grep 确认）。对照 4731 行 `media_file.change` 用内联 lambda，正确。
- 影响：在这三个栏目上传文件时回调抛 `NameError`，状态栏报错。
- 修复：定义 `_simple_status` 或统一改为内联 lambda。

### ⚠️ 3. diarization 空 asr_model 校验误报
- 位置：`app/openai_api/workflow_service.py:334-343`（校验）；`workflow_ui.py:98`（前端默认空串）；`workflow_runner.py:571`、`server.py:1523`（执行层空回退）
- 证据：`DiarizationConfig.asr_model` 默认 `""`（设计为复用 primary 结果），执行层均做 `asr_model or primary.model` 回退；但校验时 `model_capabilities.get("")` 返回 None → 必然追加 `MODEL_NOT_FOUND`。前端默认也传空串（`str(values.get("diarization_asr_model") or "")`），用户启用说话人分离但未选辅助模型即触发。
- 影响：校验与执行不一致，默认配置下 diarization 无法启用。现有测试全部显式传了 asr_model（test_workflow_runner.py:76/325、test_workflow_ui.py:35），未覆盖空串。
- 修复：`asr_model` 为空时跳过能力校验（或按 primary 模型校验）；补空串测试。

### ⚠️ 4. 思维导图 fallback 树 XSS
- 位置：`app/pat_funasr_webui/fine_transcription/audio_sync_js.py:378-389`（`_render_fallback`）
- 证据：节点 `title` 未做 HTML 转义直接拼入 `fallback_html`（385 行）；同文件 markdown 分支用 `json.dumps` 转义（392 行），fallback 分支漏掉。srcdoc iframe 与父页面同源。
- 影响：LLM 输出含 `<script>`/HTML 时可注入执行。
- 修复：`html.escape(title)` 后再拼入；补转义测试（现有 test_audio_sync_security.py 只测 markmap/内联 JSON）。

### ⚠️ 5. start_services.py 启动的是 API 演示页而非正式 WebUI（遗留入口）
- 位置：`start_services.py:74-75`
- 证据：`app/openai_api/gradio_app.py` 头部 docstring 为 "Browser demo for the FunASR OpenAI-compatible API"（255 行）；`start_services.py` 以 `cwd=API_DIR` 启动它。主链路 `FunASR_pat.bat → aipython/managed_single_window_launcher.py` 不受影响。
- 修复：删除该脚本或修正指向正式 WebUI。

### ⚠️ 6. NLLB tokenizer `src_lang` 共享状态并发污染（需确认，建议加固）
- 位置：`app/openai_api/server.py:621`
- 证据：`self.tokenizer.src_lang = source_lang` 修改共享 tokenizer 实例属性；transformers 的 NllbTokenizer 的 `src_lang` 确为实例状态。是否实际出错取决于同一 NLLB 实例是否被并发用于不同源语言。
- 修复：按请求复制 tokenizer 或加锁（低成本防护）。

### ⚠️ 7. 流式会话缓存并发竞态（需确认，建议加固）
- 位置：`app/openai_api/server.py:2202-2217`
- 证据：同一 `session_id` 并发 POST 时 `state["cache"]` 无锁并发读写；前端串行推送（flushPromise）可规避，但 API 未防多客户端。
- 修复：per-session 锁。

## 二、中优先级（健壮性 / 冗余）

| # | 位置 | 问题 | 建议 |
|---|------|------|------|
| 8 | `server.py:2056-2063` | 离线 transcribe API 未校验 `overlap_seconds >= chunk_seconds`（workflow 有校验） | 补参数校验 |
| 9 | `workflow_service.py:73-87` | `_build_summary_message` 首循环构造的 `parts` 未使用（死代码） | 删除 |
| 10 | `renderers.py:307-309` | summary.md 写入未走 `_crlf`，与"统一 UTF-8 BOM + CRLF"约定不一致 | 统一 |
| 11 | `artifact_service.py:53-59` / `renderers.py:231-233,277-279` | `_crlf` 三处重复实现；`_format_timestamp_srt/_vtt` 雷同 | 抽公共工具 |
| 12 | `server.py:1762,1796-1809` | `_workflow_llm_stage` 的 `context_title` 参数从未传入（死参数） | 删除 |
| 13 | `gradio_app.py:4109-4140` | `_on_run_workflow` 轮询无总超时、无中止信号检测，任务挂死时生成器永不结束 | 加超时/中止 |
| 14 | `gradio_app.py:186-316` | 调试埋点残留（`_dbg_report` 等会向 127.0.0.1:7777 发请求） | 移除 |
| 15 | `gradio_app.py` 多处 | 死代码：`read_runtime_logs_ui_guard`、`start/stop_system_microphone_stream`、`stream_transcribe_microphone`、`update_media_preview`、`build_reserved_feature_tab`、`initialize_service_dashboard`、`safe_check`、`safe_recognize_diarization`、`stream_state` 等定义未绑定任何组件 | 清理 |
| 16 | `transcription_pipeline.py` | `run_pipeline` 被导入但只用 `run_pipeline_streaming`；`:824` `export_result(..., format)` 遮蔽内置 `format`；`:756` 明确弃用的 legacy 函数 | 清理 |
| 17 | `summary_processor.py:36-46` | `chunk_text` 无防护：`overlap >= chunk_size` 或 `chunk_size <= 0` 死循环 | 补校验 |
| 18 | `store.py:178-195` | 同类型多次 `save_llm_output` 静默覆盖 | 明确覆盖策略/告警 |
| 19 | `gradio_app.py:3200,3229` | `fetch_model_choices` 构建期重复调用两次 | 去重 |
| 20 | `gradio_app.py:4363` | 构建期枚举麦克风设备，慢机器初始化延迟 | 懒加载 |
| 21 | `translation_utils.py:370-378` | API 返回项数少于请求时 `paragraph_translations[i]` IndexError | 补边界 |
| 22 | `llm_config.py:36-43` | `.env` 值不去引号，`BASE_URL="..."` 带引号时解析失败 | 去引号 |
| 23 | `model_catalog.py:52-73` + `server.py:2041` | qwen3-asr `vad_model` 配置正确（符合历史结论：FunASR VAD 切 ~30s 段防截断），但 server.py:2041 注释"已去掉 vad_model"为过时残留 | 更新注释，不改配置 |
| 24 | `model_catalog.py:217-218` | `MODELSCOPE_MODEL_ALIASES["paraformer"]` 指向旧版模型，与 `MODEL_CONFIGS["paraformer"]`（seaco 版）不一致（死映射） | 修正/删除 |
| 25 | `scripts/batch_transcribe.py:50-53,148,170` | `.wav` 跳过转码直接使用；不同目录同名文件互相覆盖；空结果 IndexError | 补处理 |
| 26 | `run_test_all_models.ps1:51-71` | 4 个模型串行加载从不卸载易 OOM；无单模型超时 | 加卸载/超时 |
| 27 | `.gitignore` | 未忽略 `switch_model_hub.bat` 生成的 `.env.local.bat` | 补忽略 |
| 28 | `aipython/` | 约 13 个一次性诊断脚本（review_*.py、diag_*.py、e2e_review.py 等）建议归档；保留被测试引用的脚本 | 清理 |

## 三、低优先级（优化 / 风格）

- `server.py:1349/637/655`、`renderers.py:100`：函数内重复 `import time/re`。
- `server.py:686-693`：模型加载失败 `str(exc)` 可能暴露绝对路径（`/v1/models/{model}/status`），建议复用 `workflow_service._safe_error_message`。
- `workflow_runner.py:206-283 / 285-384`：两分支进度区间不一致（0.12-0.58 vs 0.12-0.42）。
- `llm_client.py:52-101`：`_fuse_state` 全局 dict 无锁（读-改-写非原子，极端并发可能少计一次；GIL 下影响轻微）。
- `batch_asr_improved.py:40` `--device` 默认 cpu（项目为 GPU 版）；与 `scripts/batch_transcribe.py` 功能重复，属遗留。
- `batch_asr_improved.py:81`、`batch_transcribe.py:187-194`：`encoding="utf-8"` 未加 BOM，与项目约定不符。
- `switch_model_hub.bat:46-55`：`:status` 后 `goto :menu` 死循环，查看状态后需再选一次才能退出。
- `run_ui_pat.bat:5`：标题写死 "(GPU)"。
- `gradio_app.py:4347`：`sources=["upload"]` 缩进错位（仅风格）。
- `gradio_app.py:2404`：翻译预览截断取头部 `[:8000]`，与其它处取尾部不一致（含"已截断"提示但看不到最新译文）。

## 四、可增加功能建议（按优先级）

1. **任务队列与持久化**：workflow 作业目前内存态（重启丢失），可加 SQLite 持久化 + 并发上限队列，支持"批量导入音频 → 逐个跑全流程"。
2. **熔断状态可视化**：`llm_client._fuse_state` 已有熔断数据，可在 WebUI 模型/服务面板展示熔断/冷却倒计时与最近失败原因。
3. **说话人音频切片导出**：已有说话人时间轴，可加"按说话人导出对应音频片段"（ffmpeg 截取），直接服务录音卡访谈复盘场景。
4. **导出格式扩展**：在 ZIP 中可选增加 `.docx`（校对稿/纪要）与 `.csv` 结构化段表。
5. **热词/敏感词热更新 API**：当前 hotword 走配置，可提供运行时更新端点。
6. **CI 增强**：`.github/workflows/ci.yml` 已有；可加覆盖率门禁（如 ≥80%）与 PR 触发时自动跑 `pytest -x -q`。
7. **WebSocket/SSE 事件订阅**：workflow 已有事件流（轮询），可提供 SSE 长连接推送，减少前端轮询。
8. **模型内存释放**：模型切用/卸载后显存释放（`torch.cuda.empty_cache()` + 引用清理），便于小显存机器多模型切换。

## 五、测试缺口（建议优先补）

- **高**：熔断激活期/过期恢复/`_mark_ok` 重置、fallback 链真实切换（现有仅测熔断窗口起点）。
- **高**：分块合并边界——`_run_asr` 分块时间戳偏移计算（本次 bug #1 即因此漏网）、远距离真重复保留、words 时间戳偏移、空/单 chunk。
- **高**：异常音频路径（文件不存在/损坏、ffprobe 失败、ASR 非 200）的降级行为；diarization 空 asr_model 校验（bug #3 漏检原因）。
- **中**：workflow ZIP 产物逐文件内容校验、翻译分块边界（`split_text_by_length` 恰好等于/超长句）、并发端点隔离、fallback XSS 转义。
- **测试自身**：6 处 sleep/轮询时序敏感测试改为事件等待；`test_pat_webui_diarization_exports.py:314,543` 硬编码绝对路径 `y:\NewStore\AI\FunASR-Portable-GPU\test\demo.wav`；`_TEST_TS` 全局后门用后不恢复；7+ 文件重复 `spec_from_file_location` 样板可抽 conftest。

## 六、总体评价

工程质量整体较高：模块职责清晰、schema 校验与参数白名单到位、事务/WAL 与 BOM/CRLF 规范执行好。最需要立即修复的是：**分块时间戳双重偏移（#1）**、**WebUI `_simple_status` 未定义（#2）**、**diarization 空模型校验（#3）** 三个直接产生错误结果/崩溃的问题，每项配回归测试；随后清理调试埋点与死代码（#13/#14/#15），补熔断与分块边界测试。

## 七、与 v1 差异（修正记录）

| 项 | v1 判断 | v2 结论 | 依据 |
|----|---------|---------|------|
| summary_processor 流式生成器异常未捕获 | 高优先级 | **证伪，撤销** | `call_llm → _call_one`（llm_client.py:158-182）捕获全部异常返回空串，不向上抛；失败块走 `ok=False` + warning 分支 |
| qwen3-asr max_new_tokens 截断保护不成立 | 质疑 | **证伪** | qwen3-asr 走 `app/funasr/models/qwen3_asr/model.py:125` 默认 `max_new_tokens=512`（原质疑误引 llm_asr/model.py:365 的 200，属另一模型家族） |
| qwen3-asr vad_model 注释 vs 实现矛盾 | 矛盾待定 | **配置正确、注释过时** | model_catalog 配置符合历史结论（FunASR VAD + batch_size_s=60）；server.py:498-510 不剥离 vad_model；矛盾方是 server.py:2041 过时注释 |
| `_fuse_state` 无锁 | 中优先级 | **降为低优先级** | GIL 下读-改-写竞态仅致计数偶发少计，影响轻微 |
| bug #1 影响面 | "离线 API 分块模式" | **精确化** | 仅 `/v1/audio/transcriptions`（>90min 或显式 chunk_enabled）；workflow 路径正确；文本合并不受影响 |
