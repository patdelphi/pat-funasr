#
输出模板/字幕（多格式）开发 Todo

目标：在现有 OpenAI 兼容接口 "/v1/audio/transcriptions" 上，增加 txt/srt/vtt/tsv/all(zip) 输出；支持句/段级时间戳；并保持对不同模型的兼容与回退。

## 0. 约束与口径

- all：返回 zip（包含 output.txt/output.json/output.srt/output.vtt/output.tsv）
- 时间戳：句/段级（优先模型原生，其次 VAD 段）
- 参数：仅开放白名单，避免任意 kwargs 注入
- 新功能：先写测试，再实现

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

## 4. 冒烟校验（幂等）

- `python -m compileall "app"`
- `python -m unittest`

## 5. 文档同步（仅更新既有 Docs）

- 更新 "Docs/api.md"：补充 response_format 与新增参数说明
- 更新 "Docs/model-capability-matrix.md"：补充“字幕输出回退策略”一节
