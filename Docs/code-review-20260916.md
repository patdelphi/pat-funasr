# 代码审查报告（2026-09-16）

> 审查范围：`app/`（业务代码约 1.5 万行，不含 `app/funasr` 第三方库）、`tests/`（33 个测试文件）、`scripts/`、`aipython/`、启动脚本与配置。
> 审查方式：4 个子代理分模块只读审查 + 主代理对关键问题逐行复核（标注「已确认」的为本轮复核结论，「需确认」的为待定）。
> 状态标记：⚠️ 高优先级（Bug/逻辑错误）｜🔶 中优先级（健壮性/冗余）｜🔹 低优先级（优化/风格）

## 一、高优先级（Bug / 逻辑错误）

### ⚠️ 1. 离线 API 分块模式时间戳双重偏移（已确认）
- 位置：`app/openai_api/server.py:1375-1381` + `app/pat_funasr_webui/fine_transcription/transcription_pipeline.py:144-159`
- 现象：`_run_asr` 分块分支先在第 1375-1377 行手动给每块 segments 加 `offset`，随后又把 `offsets` 传给 `_merge_chunk_segments`（第 1381 行），而该函数内部再次加偏移（pipeline 144-146 行）+ words 级再次偏移（148-159 行）。
- 影响：`/v1/audio/transcriptions` 在 `chunk_enabled` 时，第 2+ 块时间轴按倍数错乱（第 3 块约 +480s 而非 +240s），SRT/VTT 字幕时间戳错误；文本合并不受影响（文本只按指纹去重）。
- 对照：`_workflow_transcribe_model`（server.py:1638-1649）不预加偏移、只依赖 `_merge_chunk_segments`，是正确的。建议删除 server.py 中的预加偏移循环。
- 测试缺口：`_call_asr_chunked`/`_run_asr` 分块主链路时间戳无测试，未能暴露。

### ⚠️ 2. WebUI 上传音频回调 NameError（已确认）
- 位置：`app/pat_funasr_webui/gradio_app.py:4738/4744/4755`
- 现象：`stream_media_file` / `emotion_media_file` / `diarization_media_file` 三个上传回调引用 `_simple_status`，全项目搜索**无此函数定义**（已确认仅 3 处调用）。
- 影响：在这三个栏目上传文件时 Gradio 回调抛 `NameError`，状态栏报错。第 4731 行 `media_file.change` 用的是内联 lambda，正确。
- 建议：定义 `_simple_status` 或统一改为内联 lambda。

### ⚠️ 3. diarization 空 asr_model 校验误报（已确认）
- 位置：`app/openai_api/workflow_service.py:334-343`
- 现象：`DiarizationConfig.asr_model` 默认 `""`（第 182 行，设计为复用 primary 结果），但校验时 `model_capabilities.get("")` 返回 None → 必然追加 `MODEL_NOT_FOUND`。执行层（`workflow_runner.py:571`、`server.py:1523`）均做了 `asr_model or primary.model` 回退，校验与执行不一致。
- 影响：API 直调默认 diarization 配置（asr_model 留空）时校验失败，diarization 无法启用；测试全部显式传了 `asr_model`，未覆盖空串。
- 建议：`asr_model` 为空时跳过该模型能力校验（或按 primary 模型校验）。

### ⚠️ 4. 思维导图 fallback 树 XSS（已确认）
- 位置：`app/pat_funasr_webui/fine_transcription/audio_sync_js.py:378-389`（`_render_fallback`）
- 现象：节点 `title` 未做 HTML 转义直接拼入 `fallback_html`；同文件 `_to_markdown` 走了 `json.dumps` 转义（392 行），fallback 分支漏掉。srcdoc iframe 与父页面同源，LLM 输出含 `<script>` 时可执行。
- 建议：`html.escape(title)` 后再拼入。

### ⚠️ 5. NLLB tokenizer 共享状态并发污染（需确认，风险中高）
- 位置：`app/openai_api/server.py:621`
- 现象：`self.tokenizer.src_lang = source_lang` 修改共享对象；并发翻译请求（`/v1/translations` + workflow 翻译）互相覆盖 `src_lang`，可能译文语言错乱。
- 建议：复制 tokenizer 或按请求加锁。

### ⚠️ 6. 流式会话并发竞态（需确认，风险中）
- 位置：`app/openai_api/server.py:2202-2217`
- 现象：同一 `session_id` 并发 POST 时 `state["cache"]` 无锁并发读写。
- 建议：per-session 锁；前端串行可规避，但 API 未防多客户端。

### ⚠️ 7. start_services.py 启动的是 API 演示页而非正式 WebUI（已确认）
- 位置：`start_services.py:74-75`
- 现象：以 `cwd=API_DIR` 运行 `gradio_app.py`，解析到 `app/openai_api/gradio_app.py`（255 行 OpenAI API 演示页），而非正式 WebUI `app/pat_funasr_webui/gradio_app.py`（5033 行）。主链路（`FunASR_pat.bat` → `managed_single_window_launcher.py` → `run_ui_pat.bat`）不受影响，但该旧入口误导性强。
- 建议：删除 `start_services.py` 或修正指向。

## 二、中优先级（健壮性 / 冗余）

| # | 位置 | 问题 | 建议 |
|---|------|------|------|
| 8 | `server.py:2056-2063` | 离线 transcribe API 未校验 `overlap_seconds >= chunk_seconds`（workflow 有校验） | 补参数校验 |
| 9 | `llm_client.py:52-101` | `_fuse_state` 全局 dict 无锁，多任务并发计数竞态 | 加锁 |
| 10 | `workflow_service.py:73-87` | `_build_summary_message` 首循环构造的 `parts` 未使用（死代码） | 删除 |
| 11 | `renderers.py:307-309` | summary.md 写入未走 `_crlf`，与"统一 UTF-8 BOM + CRLF"约定不一致 | 统一 |
| 12 | `artifact_service.py:53-59` / `renderers.py:231-233,277-279` | `_crlf` 三处重复实现；`_format_timestamp_srt/_vtt` 雷同 | 抽公共工具 |
| 13 | `server.py:1762,1796-1809` | `_workflow_llm_stage` 的 `context_title` 参数从未传入（死参数） | 删除 |
| 14 | `summary_processor.py:89-196,273-291` | 流式生成器对 `call_llm` 异常未捕获，单块网络失败中断整个流水线（与"熔断后跳过该块"设计不符） | 补 try/except |
| 15 | `gradio_app.py:4109-4140` | `_on_run_workflow` 轮询无总超时、无中止信号检测，任务挂死时生成器永不结束 | 加超时/中止 |
| 16 | `gradio_app.py:186-316` | 调试埋点残留（`_dbg_report` 等会向 127.0.0.1:7777 发请求） | 移除 |
| 17 | `gradio_app.py` 多处 | 死代码：`read_runtime_logs_ui_guard`、`start/stop_system_microphone_stream`、`stream_transcribe_microphone`、`update_media_preview`、`build_reserved_feature_tab`、`initialize_service_dashboard`、`safe_check`、`safe_recognize_diarization`、`stream_state` 等定义未绑定任何组件 | 清理 |
| 18 | `transcription_pipeline.py` | `run_pipeline` 被导入但只用 `run_pipeline_streaming`；`:824` `export_result(..., format)` 遮蔽内置 `format`；`:756` 明确弃用的 legacy 函数 | 清理 |
| 19 | `summary_processor.py:36-46` | `chunk_text` 无防护：`overlap >= chunk_size` 或 `chunk_size <= 0` 死循环 | 补校验 |
| 20 | `store.py:178-195` | 同类型多次 `save_llm_output` 静默覆盖 | 明确覆盖策略/告警 |
| 21 | `gradio_app.py:3200,3229` | `fetch_model_choices` 构建期重复调用两次 | 去重 |
| 22 | `gradio_app.py:4363` | 构建期枚举麦克风设备，慢机器初始化延迟 | 懒加载 |
| 23 | `translation_utils.py:370-378` | API 返回项数少于请求时 `paragraph_translations[i]` IndexError | 补边界 |
| 24 | `llm_config.py:36-43` | `.env` 值不去引号，`BASE_URL="..."` 带引号时解析失败 | 去引号 |
| 25 | `model_catalog.py:52-73` | qwen3-asr 仍配置 `vad_model`，与 `server.py:2041` 附近"已去掉 vad_model"注释矛盾（需确认设计意图） | 对齐注释/实现 |
| 26 | `model_catalog.py:217-218` | `MODELSCOPE_MODEL_ALIASES["paraformer"]` 指向旧版模型，与 `MODEL_CONFIGS["paraformer"]`（seaco 版）不一致（死映射） | 修正/删除 |
| 27 | `scripts/batch_transcribe.py:50-53,148,170` | `.wav` 跳过转码直接使用；不同目录同名文件互相覆盖；空结果 IndexError | 补处理 |
| 28 | `run_test_all_models.ps1:51-71` | 4 个模型串行加载从不卸载易 OOM；无单模型超时 | 加卸载/超时 |
| 29 | `.gitignore` | 未忽略 `switch_model_hub.bat` 生成的 `.env.local.bat` | 补忽略 |
| 30 | `aipython/` | 约 13 个一次性诊断脚本（review_*.py、diag_*.py、e2e_review.py 等）建议归档；保留被测试引用的脚本 | 清理 |

## 三、低优先级（优化 / 风格）

- `server.py:1349/637/655`、`renderers.py:100`：函数内重复 `import time/re`。
- `server.py:686-693`：模型加载失败 `str(exc)` 可能暴露绝对路径（`/v1/models/{model}/status`），建议复用 `workflow_service._safe_error_message`。
- `workflow_runner.py:206-283 / 285-384`：两分支进度区间不一致（0.12-0.58 vs 0.12-0.42）。
- `batch_asr_improved.py:40` `--device` 默认 cpu（项目为 GPU 版）；与 `scripts/batch_transcribe.py` 功能重复，属遗留。
- `batch_asr_improved.py:81`、`batch_transcribe.py:187-194`：`encoding="utf-8"` 未加 BOM，与项目约定不符。
- `switch_model_hub.bat:46-55`：`:status` 后 `goto :menu` 死循环，查看状态后需再选一次才能退出。
- `run_ui_pat.bat:5`：标题写死 "(GPU)"。

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
- **高**：分块合并边界——远距离真重复保留、words 时间戳偏移、空/单 chunk、`_run_asr` 分块偏移计算（本次 bug #1 即因此漏网）。
- **高**：异常音频路径（文件不存在/损坏、ffprobe 失败、ASR 非 200）的降级行为。
- **中**：workflow ZIP 产物逐文件内容校验、翻译分块边界（`split_text_by_length` 恰好等于/超长句）、并发端点隔离。
- **测试自身**：6 处 sleep/轮询时序敏感测试改为事件等待；`test_pat_webui_diarization_exports.py:314,543` 硬编码绝对路径 `y:\NewStore\AI\FunASR-Portable-GPU\test\demo.wav`；`_TEST_TS` 全局后门用后不恢复；7+ 文件重复 `spec_from_file_location` 样板可抽 conftest。

## 六、总体评价

工程质量整体较高：模块职责清晰、schema 校验与参数白名单到位、事务/WAL 与 BOM/CRLF 规范执行好。最需要立即修复的是：**分块时间戳双重偏移（bug #1）**、**WebUI `_simple_status` 未定义（bug #2）**、**diarization 空模型校验（bug #3）** 三个直接产生错误结果/崩溃的问题；随后清理调试埋点与死代码（#15/#16/#17），补上熔断与分块边界测试（第五部分高优先项）。

## 七、第二轮验证记录（2026-09-16，修复前）

> 验证方式：主代理逐行复核关键代码路径 + 查阅 `app/funasr` 第三方库源码（视为模型相关文档，含 `qwen3_asr/model.py`、`auto/auto_model.py`）+ 与项目历史结论（memory）交叉比对。

### 已确认成立（附证据）

1. **分块时间戳双重偏移（#1）——成立，影响面精确化**
   - 证据链：`_run_asr`（server.py:1375-1377）手动给每块 segments 加 `offset`，随后 1381 行把 `all_segs` 与 `offsets` 传给 `_merge_chunk_segments`（transcription_pipeline.py:144-159），该函数内部对 segments 与 words 再次加偏移。`_merge_chunk_segments` 的 docstring（pipeline:126-130）明确要求传入**未加偏移**的 segments。
   - 影响范围：`_run_asr` 仅被 `/v1/audio/transcriptions` 调用（server.py:2066-2077）；workflow 路径（`_workflow_transcribe_model`，server.py:1513 起）有自己的分块循环、不预加偏移、仅依赖 `_merge_chunk_segments`（1645-1649），**正确**。
   - 触发条件：transcribe API 且 `chunk_enabled=true`（显式传参）或音频 >90min（sensevoice/paraformer 等自动开启，server.py:2056-2063）。此时第 2+ 块 SRT/VTT 时间戳按倍数错乱；文本合并不受影响。

2. **WebUI `_simple_status` 未定义（#2）——成立**
   - 全项目 grep 仅 3 处调用（gradio_app.py:4738/4744/4755），无定义。`media_file.change`（4731 行）用内联 lambda 是正确对照。

3. **diarization 空 asr_model 校验误报（#3）——成立，影响面扩大**
   - `DiarizationConfig.asr_model` 默认 `""`（workflow_service.py:182），执行层做 `or primary.model` 回退（workflow_runner.py:571、server.py:1523）。
   - 前端 workflow_ui.py:98 默认也是 `str(values.get("diarization_asr_model") or "")` → 用户启用说话人分离但未选辅助模型时，提交的 asr_model 为空串 → validate（workflow_service.py:335 `model_capabilities.get("")` → None）报 `MODEL_NOT_FOUND`，校验失败。
   - 现有测试全部显式传了 asr_model（test_workflow_runner.py:76/325、test_workflow_ui.py:35），未覆盖空串，故漏检。

4. **思维导图 fallback XSS（#4）——成立**
   - `_render_fallback`（audio_sync_js.py:378-389）把节点 `title` 直接拼入 HTML（385 行），未做转义；同文件 markdown 分支用 `json.dumps` 转义（392 行）形成对照。srcdoc iframe 与父页面同源，LLM 输出含 `<script>` 时存在注入风险。

5. **`start_services.py` 启动 API 演示页而非正式 WebUI（#7）——成立（遗留入口）**
   - `app/openai_api/gradio_app.py` 头部 docstring 明确为 "Browser demo for the FunASR OpenAI-compatible API"（255 行）；`start_services.py:74-75` 以 `cwd=API_DIR` 启动它。主链路 `FunASR_pat.bat → aipython/managed_single_window_launcher.py` 不受影响。

### 已修正或证伪（原报告有误）

6. **summary_processor 流式生成器"call_llm 异常未捕获"（原 #14）——证伪，撤销**
   - `call_llm` → `_call_one`（llm_client.py:158-182）对 ConnectTimeout/ReadTimeout/ConnectionError/JSON 解析/未知异常**全部捕获并返回空串**，不会向上抛异常。生成器不会因网络错误中断；失败块走 `ok=False` + warning 分支（summary_processor.py:93-95），与"熔断后跳过该块"设计一致。该项不成立。

7. **qwen3-asr "max_new_tokens=512 截断保护不成立"（原 subagent3 #2）——证伪**
   - qwen3-asr 走 `app/funasr/models/qwen3_asr/model.py:125`，`max_new_tokens = kwargs.get("max_new_tokens", 512)` 默认 512；model_catalog.py:58 注释成立。（原质疑引用的 `llm_asr/model.py:365` 默认 200 是另一模型家族，不适用于 qwen3-asr。）

8. **qwen3-asr `vad_model` "注释 vs 实现矛盾"（原 #25）——修正为：配置正确、注释过时**
   - `model_catalog.py:60/71` 配置 `vad_model=fsmn-vad` 是当前设计（与项目历史结论一致：FunASR VAD 切 ~30s 段避免 max_new_tokens=512 截断，外层 chunking 关闭）；`server.py:498-510` 仅对 vad_model 补 `vad_model_path`，**不剥离**。
   - 因此矛盾方是 server.py:2041 的注释"MODEL_CONFIGS 已去掉 vad_model"——为历史版本残留的**过时注释**，非代码 bug。建议改注释，不改配置。

### 保持"需确认"（无决定性证据，修复时先加防护即可）

9. **NLLB tokenizer `src_lang` 并发污染（#5）**：`self.tokenizer.src_lang = source_lang`（server.py:621）修改共享 tokenizer 实例属性，transformers 的 NllbTokenizer 确有此状态；是否实际出错取决于同一 NLLB 实例是否被并发用于不同源语言翻译。建议按请求复制 tokenizer 或加锁（低成本防护）。
10. **流式会话缓存无锁（#6）**：同一 `session_id` 多客户端并发 POST 时 `state["cache"]` 竞态；前端串行推送可规避。建议 per-session 锁。
11. **`_fuse_state` 全局无锁（#9）**：读-改-写非原子，极端并发下计数可能少计一次；影响轻微，降为低优先级。

### 验证后确认的关键修复优先级（不变）

分块双重偏移（#1）→ `_simple_status`（#2）→ diarization 空校验（#3），配回归测试（第五部分高优先项：`_run_asr` 分块时间戳、diarization 空 asr_model 校验、fallback XSS 转义）。
