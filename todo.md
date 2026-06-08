﻿#
输出模板/字幕（多格式）开发 Todo

目标：修复并增强输出质量：txt/srt/vtt/tsv/all(zip) 输出具备“标点 + 分段 + 合理时间戳”；修复 "fun-asr-nano" 无输出；并允许在线下载/更新到最新可用模型。

## 0. 约束与口径

- all：返回 zip（包含 output.txt/output.json/output.srt/output.vtt/output.tsv）
- 时间戳：句/段级（优先 sentence_info，其次 VAD 段；禁止整段 0~duration 兜底当作正常结果）
- 参数：仅开放白名单，避免任意 kwargs 注入
- 新功能：先写测试，再实现
- 批量脚本：不再跑 "paraformer-en"，只跑 "sensevoice/paraformer/fun-asr-nano"
- 模型下载：开放在线下载，默认允许更新到最新（disable_update=false）

## 1. 补测试框架（最小化）

- 新增 "tests/"（使用 Python 内置 unittest，不引入 pytest 依赖）
- 新增渲染器单测夹具（来自 "Docs/upgrade-plan-output-template.md" 的验收样例）

验收：

- 运行 `python -m unittest discover -s "tests"` 通过

## 2. 新增输出渲染器模块（纯后处理）

- 新增 "app/openai_api/renderers.py"（或同目录模块）
  - 输入：segments[{start,end,text}]、full_text、meta
  - 输出：txt/json/srt/vtt/tsv
  - 提供 `render_all_zip()`：打包 zip（内含多文件）
- 统一时间戳格式化：SRT（逗号毫秒），VTT（点毫秒）
- 断行规则：max_line_width/max_words_per_line/max_line_count（先支持最常用的 max_line_width）

验收：

- 单测覆盖：srt/vtt/tsv/json/txt/all(zip)

## 3. 扩展 API：response_format 与参数白名单

- 扩展 "/v1/audio/transcriptions"：
  - 新增 response_format：txt/srt/vtt/tsv/all
  - 新增字幕参数：max_line_width/max_words_per_line/max_line_count
  - 新增 VAD 预设：vad_preset（default/anti_hallucination）并映射到 vad_kwargs 白名单
- 兼容性策略：
  - 如果模型未返回 sentence_info：使用 VAD 段级时间戳生成字幕
  - 如果连 VAD 段也不可用：降级为无时间戳 txt/json（并在 verbose_json.meta 里标明降级原因）

验收：

- API 级单测：对不同 response_format 的分支输出结构正确

## 4. 批量脚本修复与复跑

- 更新 "run_test_all_models.ps1"
  - 移除 "paraformer-en"
  - 默认开启 sentence_timestamp（用于分段时间戳）
  - 为 "sensevoice" 增加 punc_model（保证 txt 有标点）
  - 修复 "fun-asr-nano" 无输出：捕获异常并把 traceback 写入 "run.log"，确保失败可定位
  - 允许下载/更新：移除“离线缓存缺失就 skip”的逻辑，保持可重复运行（覆盖输出文件）

验收：

- 运行脚本后：
  - "test\\sensevoice\\"、"test\\paraformer\\"、"test\\fun-asr-nano\\" 均生成 "1/2" 的 txt/tsv/srt/vtt/json/zip/wav + run.log
  - srt/vtt 不是单条 0~duration，而是多条 cue
  - txt 含标点且有分段（至少多段）

## 5. 冒烟校验（幂等）

- `python -m compileall "app"`
- `python -m unittest`

## 6. 文档同步（仅更新既有 Docs）

- 更新 "Docs/api.md"：补充 response_format 与新增参数说明
- 更新 "Docs/model-capability-matrix.md"：补充“字幕输出回退策略”一节
