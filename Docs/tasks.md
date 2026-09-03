# Pat-FunASR 优化与新功能计划

## 执行优先级排序（从高到低）

### Phase 1：高优优化（不改接口、加内部复用和容错）

| # | 任务 | 预估 | 风险 | 验证方式 |
|---|------|------|------|----------|
| 1 | 抽 LLM client 公共模块 (`app/openai_api/llm_client.py`) + fallback 链 | 45min | 低 | call_llm 已有单测，抽出后保持行为一致 + 新增 fallback 测试 |
| 2 | 离线识别 API 暴露分块参数 (`chunk_enabled`/`chunk_seconds`/`overlap_seconds`) | 30min | 低 | pytest + 真实 API 调用 |
| 3 | reviewer 并行执行 (`ThreadPoolExecutor` 并行跑多个校对模型) | 30min | 低 | 现有 70min 录音双模型工作流，看是否提速 |

### Phase 2：新功能试点（用户感知明显）

| # | 任务 | 预估 | 风险 | 验证方式 |
|---|------|------|------|----------|
| 4 | 关键词抽取 + 高亮搜索（LLM 后加 `extract_keywords` + 前端搜索框） | 60min | 中 | LLM 返回关键词 JSON → 前端 transcript 文字高亮匹配 |
| 5 | 转录结果 SQLite 存储 + 版本管理（避免重复跑同一文件） | 60min | 中 | 复用已有 store.py 模式，hash 文件作为 key |

### Phase 3：中优优化（代码质量）

| # | 任务 | 预估 | 风险 | 验证方式 |
|---|------|------|------|----------|
| 6 | server.py 拆分（transcribe_endpoints / workflow_endpoints / translate_endpoints） | 90min | 高 | 保持所有 import 正确 + 全量 pytest 通过 |
| 7 | GPU 模型 LRU cache（最多 2-3 个常驻，LRU 淘汰冷模型） | 60min | 中 | 加载多模型场景下 OOM 不再发生 |

### Phase 4：低优新功能（锦上添花）

| # | 任务 | 预估 | 风险 | 验证方式 |
|---|------|------|------|----------|
| 8 | 批量 API (`POST /v1/funasr/batch` 多文件并行提交) | 60min | 中 | 复用 workflow 队列 |
| 9 | 历史记录查询 (`GET /v1/funasr/workflows/history`) | 30min | 低 | SQLite 加 status 表 |
| 10 | 转录结果分享链接（带 token 的只读 URL） | 45min | 中 | 新增轻量 HTTP 端点 |

## 本轮执行范围

**先执行 Phase 1（#1-#3）**，全部完成后跑全量 pytest，确认无回归。

Phase 1 每个任务完成后 commit & push，保持原子提交。

## 不做的事

- gradio_app.py 拆分（4867 行，改动面太大，等 Phase 1-3 稳定后再评估）
- markmap 节点编辑（前端复杂度高，ROI 不确定）
- 说话人修正 + 重新对齐（需要前端新组件 + 重新对齐 API，改动大）
