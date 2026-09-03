# Pat-FunASR 架构与设计

## 四层架构

```
┌─────────────────────────────────────────────────────────────┐
│  启动层                                                       │
│  FunASR_pat.bat / run_api.bat / run_ui_pat.bat / start_services.py │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  服务层（FastAPI + Gradio）                                     │
│                                                              │
│  server.py（FastAPI）            gradio_app.py（Gradio）    │
│  ├─ /v1/audio/transcriptions     ├─ 转录工作台                │
│  ├─ /v1/funasr/workflows         ├─ 实时识别                  │
│  ├─ /v1/funasr/translate         ├─ 媒体与文本工具             │
│  ├─ /v1/funasr/diarization       └─ 模型与服务                │
│  └─ /v1/funasr/emotion                                        │
└──────────────────────┬──────────────────────────────────────┘
                       │ 调用
┌──────────────────────▼──────────────────────────────────────┐
│  模型层（FunASR AutoModel + 自定义能力）                          │
│                                                              │
│  ASR: SenseVoice / Paraformer / Fun-ASR-Nano / Qwen3-ASR      │
│  说话人: cam++ / eres2net                                     │
│  翻译: NLLB-200-Distilled 600M / 1.3B                        │
│  情感: emotion2vec-plus-large                                │
│  LLM 后处理: call_llm (外部 API, .env 配置)                     │
└──────────────────────┬──────────────────────────────────────┘
                       │ 读取
┌──────────────────────▼──────────────────────────────────────┐
│  运行时层                                                      │
│                                                              │
│  runtime/python  (嵌入式 Python + PyTorch CUDA)               │
│  C:\Users\<你>\.cache\modelscope\hub\models  (全局模型缓存)     │
│  .env  (LLM API Key, 已 .gitignore)                           │
└─────────────────────────────────────────────────────────────┘
```

## 工作流引擎

`workflow_runner.run_workflow(context, runtime)` 是核心编排器，串联 8 个阶段：

```
prepare → preprocess → transcription(primary+reviewers) → reconciliation
       → diarization → llm_stages(proofread/summary/mindmap) → translation → emotion → export
```

**关键设计**：所有阶段通过 `WorkflowRuntime` 注入回调，测试可替换为 FakeModel，生产环境由 `server.py` 注入真实实现。

### 工作流调度链

```
POST /v1/funasr/workflows
  → WorkflowManager.submit()  (入队, 返回 job_id)
  → 后台线程 _run_workflow_job()
    → workflow_runner.run_workflow(context, WORKFLOW_RUNTIME)
      → _workflow_transcribe_model()  (分块 ASR)
      → reconciliation_service.reconcile_transcriptions()
      → runtime.diarize()             (cam++)
      → _run_llm_stages()             (proofread / summary / mindmap)
      → _workflow_translate()         (NLLB, 自动分块)
      → artifact_service.write_artifacts()  (ZIP 9 文件)
```

## 代码复用策略（关键）

长音频 ASR 分块和 NLLB 翻译分块**不是**精细转录工作流独有，而是**自动下沉到公共层**，所有入口受益：

| 能力 | 下沉位置 | 覆盖路径 |
|------|----------|----------|
| **ASR 自动分块**（>5min） | `server.py` `/v1/audio/transcriptions` 端点 + `_workflow_transcribe_model` | 离线识别 API + 精细转录工作流 |
| **NLLB 翻译分块**（≤500字/块） | `NLLBTranslationModel.translate()` | 工作流翻译 + 独立翻译 Tab + API `/v1/funasr/translate` |
| **LLM 熔断 / connect+read 超时 / enable_thinking=False** | `summary_processor.call_llm()` | 校对 + 纪要 + 脑图 三条 LLM 调用路径 |
| **校对回填 `_redistribute_refined_to_segments`** | `workflow_runner`（纯函数） | 任何 scope=refined/all 的 LLM 后处理场景，import 即可用 |

**设计原则**：能下沉到底层类/方法的不分层调用，能自动触发的不加额外参数。这样新增入口（如批量 API、新 UI Tab）时自动继承稳定性，不会遗漏。

### ASR 分块数据流

```
原始音频 (2h43m)
  → _split_audio_ffmpeg()  ffmpeg -ss 切 240s + 10s 重叠
    → chunk_001.wav (240s)
    → chunk_002.wav (240s, offset=230s)
    → ... 共 42 块
  → 每块 asr_model.generate()
  → _merge_chunk_segments(all_segs, offsets, overlap_seconds=10)
    → 给每段加 offset
    → 文本前 40 字指纹 + 2×重叠时间窗口去重
    → 按 start 排序
  → 合并后 segments: 2659 段 × 56936 字
```

### NLLB 翻译分块数据流

```
原文 (28531 字)
  → NLLBTranslationModel.translate(text, src, tgt)
    → 短文本 ≤500 字: 直接 translate_one()
    → 长文本: 按换行 → 按。！？!?\. 切分，累计 ≤500 字成一块
      → chunk_1 (487 字) → translate_one()
      → chunk_2 (492 字) → translate_one()
      → ... 共 59 块
    → "\n".join(translated_parts)
  → 译文: 40948 字
```

### LLM 熔断机制

```
call_llm(prompt, ...)
  → 检查 CIRCUIT_BREAKER: consecutive_failures >= 2 → short-circuit，返回 ""
  → requests.post(timeout=(10, 300))  # connect 10s, read 300s
  → 成功: consecutive_failures = 0
  → 失败: consecutive_failures += 1
    → >= 2 → 激活 5 分钟熔断，后续直接短路
```

### 校对回填数据流

```
scope=refined:
  result["segments"] = [seg1, seg2, ..., segN]  # 原 segments, 各有 text
  result["text"] = ""  # 空
  → call_llm(scope=refined, stage_input=join(seg.text))
  → result["text"] = "校对后全文"
  → _redistribute_refined_to_segments(segments, "校对后全文")
    → 按原 segments[i].text 长度比例切分 "校对后全文"
    → 回填 seg["text"] = piece
  → result["text"] == join(seg.text)  # 精确一致
```

## 分块失败回退

ffmpeg 分块可能因以下原因失败：
- 假音频（测试用例传入 `b"fake-audio"`）
- 文件损坏或格式异常
- ffmpeg 未安装

**处理**：`_workflow_transcribe_model` 中 try/except 包裹分块逻辑，失败时自动 fallback 到单块 `[(source_path, 0.0)]`，打 warning 日志，任务继续。生产环境不会因 ffmpeg 异常中断。

## 稳定性设计

| 机制 | 位置 | 说明 |
|------|------|------|
| LLM connect/read 超时拆分 | `summary_processor.call_llm` | `timeout=(10, 300)` 避免单 300s 假死 |
| LLM 熔断 | `summary_processor.call_llm` | consecutive_failures ≥ 2 → 5 分钟短路 |
| LLM enable_thinking=False | `summary_processor.call_llm` | qwen3 推理模型关闭 thinking，避免 reasoning_tokens 挤占 content |
| SQLite WAL | `store.py` | `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL` |
| 所有 API 异常处理 | `server.py` | HTTP 400/500 响应，不抛到上层 |
| reviewer 失败 skip | `workflow_runner` | `skip_failed_reviewer=true` 时 reviewer 失败不阻断主模型 |
| 分块失败 fallback | `server.py` | ffmpeg 分块失败 → 单块 |
| NLLB translate 内部分块 | `NLLBTranslationModel.translate` | 长文本自动分块，外部无需关心 |
| markmap iframe srcdoc | `audio_sync_js.get_markmap_html` | Gradio 6.x 不执行内联 script → iframe srcdoc |
| markmap CDN 失败兜底 | `audio_sync_js.get_markmap_html` | 纯 HTML 树状视图渲染 |

## 风险与已知限制

| 限制 | 影响 | 说明 |
|------|------|------|
| Diarization 聚类必须 CPU | cam++ 占 20% 时间 | scipy.linalg.eigh / sklearn KMeans / HDBSCAN 是 CPU-only 算法，无法 GPU 加速 |
| LLM 熔断只覆盖 summary_processor | 独立 LLM 调用不受保护 | 如需独立调用 LLM 复用熔断，把 call_llm 提取到公共 util |
| markmap 依赖 JS CDN | 离线环境无法渲染 | 兜底有纯 HTML 树状视图 |
| FunASR 自动加载优先 hub="hf" | 启动时会尝试 HF | MODEL_CONFIGS 已设置 hub="ms" + disable_update=True |
