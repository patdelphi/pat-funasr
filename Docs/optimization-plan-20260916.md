# 优化执行计划（2026-09-16）

> 依据：`Docs/code-review-20260916-v2.md`（最终正确版审查报告）
> 原则：最小改动、只改需求相关、每个修复配回归测试、修复后跑 pytest 白名单校验、不动第三方 `app/funasr` 库

## 执行范围（分阶段）

### 阶段 1：高优先级 Bug 修复（7 项，含回归测试）

| # | 修复项 | 文件 | 动作 |
|---|--------|------|------|
| 1 | 分块时间戳双重偏移 | `app/openai_api/server.py:1375-1377` | 删除手动加 offset 循环；新增 `_run_asr` 分块时间戳回归测试 |
| 2 | `_simple_status` 未定义 | `app/pat_funasr_webui/gradio_app.py:4738/4744/4755` | 定义 `_simple_status` 或改内联 lambda |
| 3 | diarization 空 asr_model 校验误报 | `app/openai_api/workflow_service.py:334-343` | asr_model 为空时跳过能力校验；新增空串测试 |
| 4 | 思维导图 fallback XSS | `app/pat_funasr_webui/fine_transcription/audio_sync_js.py:378-389` | `html.escape(title)`；扩展转义测试 |
| 5 | start_services.py 启动演示页 | `start_services.py:74-75` | 修正为正式 WebUI 或删除脚本 |
| 6 | NLLB tokenizer src_lang 并发污染 | `app/openai_api/server.py:621` | 按请求复制 tokenizer 或加锁 |
| 7 | 流式会话缓存并发竞态 | `app/openai_api/server.py:2202-2217` | per-session 锁 |

> 顺序安排：server.py（#1/#6/#7）一次性改完 → gradio_app.py（#2）→ workflow_service.py（#3）→ audio_sync_js.py（#4）→ start_services.py（#5）。同文件问题合并处理，避免多次往返。

### 阶段 2：中优先级健壮性修复（无风险项优先）

- 参数校验：`server.py:2056-2063` overlap/chunk_seconds（对齐 workflow 校验）
- 死代码删除：`workflow_service.py:73-87`（未用 parts）、`server.py:1762/1796`（context_title 死参数）
- 格式统一：`renderers.py:307-309` summary.md 走 `_crlf`；`llm_config.py:36-43` .env 去引号
- 边界防护：`summary_processor.py:36-46` chunk_text 参数校验；`translation_utils.py:370-378` IndexError
- 重复调用：`gradio_app.py:3200/3229` fetch_model_choices 去重；`:4363` 麦克风枚举懒加载
- 注释修正：`model_catalog.py:52-73` / `server.py:2041` vad_model 过时注释；`model_catalog.py:217-218` 死映射
- 脚本修复：`scripts/batch_transcribe.py` wav 转码/重名覆盖/空结果；`run_test_all_models.ps1` 卸载与超时；`switch_model_hub.bat` 死循环
- `.gitignore` 补 `.env.local.bat`

### 阶段 3：测试补强（先补漏网 bug 的回归防线）

- 熔断：激活期短路、过期恢复、`_mark_ok` 重置、fallback 链真实切换
- 分块合并边界：`_run_asr` 分块时间戳、远距离真重复保留、words 偏移、空/单 chunk
- 异常音频路径：文件不存在/损坏、ffprobe 失败、ASR 非 200
- 修复测试自身：硬编码绝对路径、`_TEST_TS` 后门恢复、sleep 轮询改事件等待

### 阶段 4：清理（保守，删除前确认）

- 调试埋点：`gradio_app.py:186-316` `_dbg_*` 系列
- 死代码：`gradio_app.py` 未绑定组件的函数清单（见 v2 报告 #15）
- 重复实现：`_crlf` 三处 → 抽公共工具（涉及多文件，需评估后执行）
- `aipython/` 13 个一次性诊断脚本归档（保留被测试引用的）
- `start_services.py` / `batch_asr_improved.py` 遗留（视阶段 1 决定）

### 阶段 5：功能新增（可选，单独确认后执行）

任务队列持久化、熔断状态可视化、按说话人导出音频切片、DOCX/CSV 导出、热词热更新 API、CI 覆盖率门禁、SSE 事件推送、模型显存释放。

## 验证方式

- 每阶段完成后：`python -m pytest tests/ -x -q` 全量回归（白名单幂等校验）
- 阶段 1 每项修复配单测，先跑对应单测再跑全量
- 危险操作（删脚本/删目录）先列清单经确认后执行
- 完成后按用户规则追加 chat_history.md

## 未决项（需用户确认）

1. 执行范围：仅阶段 1 / 阶段 1-2 / 阶段 1-4 / 全部含阶段 5
2. `start_services.py`（#5）：修正指向 还是 直接删除
3. `aipython/` 归档脚本：仅移动到 `aipython/archive/` 子目录 还是 删除
