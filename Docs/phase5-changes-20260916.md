# 阶段 5 变更摘要（2026-09-16）

> 范围：Docs/optimization-plan-20260916.md 阶段 5 功能新增 8 项（5a-5h + 5i 收尾），用户确认执行、跳过逐项选择。
> 原则：最小改动、中文注释、新功能先写测试、SQLite WAL + 事务、不改变默认产物集合、保留现有轮询/轮询测试契约。

## 总览

| 项 | 功能 | 主要文件 | 新增测试数 |
|----|------|----------|-----------|
| 5a | 任务队列持久化（SQLite 重启恢复） | workflow_service.py、server.py | 5 |
| 5b | 熔断状态可视化（只读端点 + WebUI） | llm_client.py、server.py、gradio_app.py | 3 |
| 5c | 按说话人导出音频切片 | gradio_app.py | 2 |
| 5d | DOCX/CSV 导出 | renderers.py、artifact_service.py、workflow_service.py、server.py、gradio_app.py | 3+1 |
| 5e | 热词热更新 API | server.py | 4 |
| 5f | CI 覆盖率门禁 | .github/workflows/ci.yml、Docs/requirements-ci.txt | 0 |
| 5g | SSE 事件推送 | server.py | 2 |
| 5h | 模型显存释放 | server.py | 4 |
| 5i | 全量回归 + 文档收尾 | todo.md、chat_history.md | 0 |

## 5a 任务队列持久化

- `app/openai_api/workflow_service.py`
  - 新增 `_JobStore`：SQLite 存储，`PRAGMA journal_mode=WAL` + `synchronous=NORMAL`，每条写入走显式事务；表 `workflow_jobs(job_id, payload, updated_at)`，payload 为 JSON 快照。
  - `WorkflowJobManager.__init__` 新增可选 `db_path` 参数；`None` 时保持纯进程内存储（兼容旧行为与既有测试）。
  - 持久化钩子：`submit`（queued 即入库）、`_execute` 的 running/completed/failed/cancelled、`cancel`、`prune_terminal_jobs`（同步删库）。
  - `_restore_jobs`：启动时从 SQLite 恢复历史任务；非终态任务统一标记 `failed`，追加 `WORKFLOW_INTERRUPTED_BY_RESTART` 事件并写回，重建 cancel_event/future/next_event_id 等运行字段。
  - `shutdown` 关闭数据库连接。
- `app/openai_api/server.py`
  - `WORKFLOW_MANAGER` 传入 `db_path`，默认 `_PROJECT_ROOT / "workspace" / "workflow_jobs.db"`，可用环境变量 `FUNASR_WORKFLOW_DB_PATH` 覆盖；workspace 已被 .gitignore 忽略。
- 测试：`tests/test_workflow_job_persistence.py`（提交即入库、终态重启恢复、崩溃任务标记失败、prune 清理、多任务恢复）。

## 5b 熔断状态可视化

- `app/openai_api/llm_client.py`
  - 新增 `fuse_state_snapshot()`：按 `{base_url}|{model}` 维度返回 `fail_streak / open_until / remaining_seconds / active / last_reason`，读取加锁。
- `app/openai_api/server.py`
  - 新增 `GET /v1/funasr/llm/fuse-state`，返回 `{"fuse_state": snapshot}`，异常处理保证 500 不外泄内部细节。
- `app/pat_funasr_webui/gradio_app.py`
  - "运行资源" tab 新增 `fuse_state_panel` Markdown；`safe_build_fuse_state_panel` 带超时与降级。
- 测试：`tests/test_llm_fuse_state_endpoint.py`（空快照、激活熔断展示、_mark_ok 重置）。

## 5c 按说话人导出音频切片

- `app/pat_funasr_webui/gradio_app.py`
  - 新增 `build_speaker_audio_slices(audio_path, segments, timestamp)`：按 speaker 分组，ffmpeg `atrim + asetpts + concat` 拼装 mp3，输出 `spk_{spk}_{ts}.mp3`；单个说话人失败跳过并记 warning，`subprocess.run(timeout=600)`。
  - `build_diarization_export_files` 新增可选 `audio_path`：提供时把切片追加进 ZIP 的 `speakers/` 目录；不传时行为不变（向后兼容）。
- 测试：`tests/test_pat_webui_diarization_exports.py` 新增切片分组与 ZIP 追加用例（mock subprocess，不依赖真实 ffmpeg）。

## 5d DOCX/CSV 导出

- `app/openai_api/renderers.py`
  - 新增 `render_csv(segments)`：表头 start/end/speaker/text，csv 模块转义。
  - 新增 `render_docx(segments, title)`：纯标准库 zipfile 拼装最小 DOCX（[Content_Types].xml / _rels/.rels / word/document.xml），XML 转义 + 剔除非法控制字符，不依赖 python-docx。
  - `render_all_zip` / `render_fine_all_zip` 新增 `extra_formats` 参数，默认不改变原 8 件套产物。
- `app/openai_api/artifact_service.py`：新增 `_EXTRA_TRANSCRIPT_FORMATS = ("csv", "docx")`，显式选择时写出；"all" 仍只展开基础 5 件套（json/txt/srt/vtt/tsv）。
- `app/openai_api/workflow_service.py`：`ExportConfig.formats` Literal 增加 `"csv", "docx"`。
- `app/openai_api/server.py`：`allowed_formats` 增加 csv/docx，响应链加 csv 文本与 docx 二进制（Content-Disposition attachment）响应。
- `app/pat_funasr_webui/gradio_app.py`：ft_export_formats CheckboxGroup choices 增加 csv/docx。
- 测试：`tests/test_renderers.py`（render_csv/render_docx/zip extra_formats）、`tests/test_artifact_service.py`（写 csv/docx 格式）。

## 5e 热词热更新 API

- `app/openai_api/server.py`
  - 新增 `_HOTWORD_OVERRIDES` 运行时覆盖表 + `_HOTWORD_LOCK`。
  - `_effective_hotword(model, explicit)` 优先级：显式传参 > 运行时覆盖 > 无。
  - 两处 `build_generate_kwargs` 调用（workflow 分块路径、/v1/audio/transcriptions 路径）改走 `_effective_hotword`。
  - 新增端点：`GET /v1/funasr/hotwords`、`PUT /v1/funasr/hotwords/{model}`（空串=清除）、`DELETE /v1/funasr/hotwords/{model}`。
- 测试：`tests/test_server_hotword_api.py`（CRUD、优先级、transcription 应用覆盖、显式优先）。

## 5f CI 覆盖率门禁

- `.github/workflows/ci.yml`：Run tests 步骤改为 `python -m pytest -q --cov=app/openai_api --cov-report=term-missing --cov-fail-under=65`。
- `Docs/requirements-ci.txt`：新增 `pytest-cov==7.1.0`。
- 阈值依据：核心 openai_api 目录本地覆盖率基线 67%，留 2% 余量；本地以 65% 门槛验证通过（66.83%）。

## 5g SSE 事件推送

- `app/openai_api/server.py`
  - 新增 `GET /v1/funasr/workflows/{job_id}/events/stream`（`text/event-stream`）：增量事件 `data: {...}` 实时下发，`keep-alive` 心跳注释行防代理超时，任务进入终态后发送 `event: done` 并自动关闭连接；任务途中被清理时发送 `event: error`。
  - 端点前置校验任务存在性，404 走响应头而非 SSE 体。
  - 原 `/events` 轮询端点保持不变（兼容现有轮询测试）。
- 测试：`tests/test_server_workflow_sse.py`（事件流 + done 关闭、未知任务 404）。

## 5h 模型显存释放

- `app/openai_api/server.py`
  - 新增 `_MODEL_LAST_USED`（registry_key → 最后使用时间）；`load_model` 缓存命中与加载完成两处刷新时间戳（每次 ASR 请求都会命中缓存）。
  - 新增 `_sweep_idle_models()`：超过 `FUNASR_MODEL_IDLE_TTL_S`（默认 1800s，0 禁用）的模型从 registry 卸载，清理 `MODEL_LOAD_STATUS`，`gc.collect()` + `torch.cuda.empty_cache()`（仅 CUDA）。
  - 新增 `_model_idle_reaper_loop` 后台守护线程（daemon），`main()` 启动。
  - 卸载后 `_model_load_state` 自动回落到非 ready，前端状态正确。
- 测试：`tests/test_server_model_idle_release.py`（过期卸载、未过期保留、TTL=0 禁用、缓存命中刷新时间戳）。

## 验证结果

- 全量回归：`python -m pytest -q` → **278 passed**（阶段 4 收尾时 254 + 本次新增 24）。
- 覆盖率门禁本地验证：`--cov=app/openai_api --cov-fail-under=65` → 66.83% 通过。
- 既有契约未破坏：workflow 校验/状态/事件/取消端点、轮询 /events、ZIP 默认 8 件套、"all" 展开基础 5 件套、测试 mock ffmpeg 等均保持。

## 未执行事项

- git commit/push：按用户规则需显式确认后执行。
- 阶段 5 之外：4c 重复工具抽取判定不执行（跨模块抽取收益低）。
