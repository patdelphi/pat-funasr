#
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

## 7. WebUI 分支（为大改做隔离）

目标：保留原 WebUI（"app/openai_api/gradio_app.py" 与 "run_ui.bat"）不动，复制一套新的 "pat-funasr WebUI" 作为后续大改入口。

计划：

- 新建目录："app/pat_funasr_webui/"
- 复制现有 UI 入口为："app/pat_funasr_webui/gradio_app.py"（先做到功能等价，可跑通）
- 新增启动脚本（不影响原脚本）：
  - "run_ui_pat.bat"：仅启动 pat WebUI
  - （可选）"FunASR_pat.bat"：同时启动 API + pat WebUI

可配置项（建议默认不与原 UI 冲突）：

- pat WebUI 默认端口：7861（原 UI 为 7860）
- API base_url：默认 http://localhost:8000（与现有一致）

验收：

- 运行 "run_ui_pat.bat" 后可打开 pat WebUI，并能正常调用 API 完成一次转写

## 8. Pat WebUI 大改（分阶段）

范围：只改 "app/pat_funasr_webui/gradio_app.py"（以及必要的同目录新模块），不动原 "app/openai_api/gradio_app.py"。

### 8.1 动态模型列表

- 启动时调用 `GET /v1/models`，将下拉框改为动态列表，并显示 ready 状态
- 刷新按钮：允许手动刷新模型列表

### 8.2 多格式输出 + 下载

- 输出格式选择：txt/srt/vtt/tsv/json/verbose_json/all(zip)
- 对非 JSON（txt/srt/vtt/tsv/zip）：
  - UI 侧改为保存二进制响应到临时文件
  - 提供下载组件（File）与预览（Textbox/Code）

### 8.3 高级参数面板

- 在 Accordion 中暴露：vad_preset/merge_vad/merge_length_s/max_line_width/hotword 等
- 请求体按白名单拼接，避免把 UI 任意字段透传到后端

### 8.4 批量/队列

- 支持多文件上传（文件列表）
- 队列执行、进度展示、失败项保留错误详情与可重试

验收（每阶段）：

- 能在 "run_ui_pat.bat" 启动的 UI 中完成一次真实转写，并产出对应格式的可下载文件

## 9. 对齐官方能力（“功能完整 WebUI”）

依据官方教程（tutorial.html）建议的核心场景，把 pat WebUI 拆成多 Tab（或多页面）：

- 离线识别（ASR）：Paraformer / SenseVoice / Fun-ASR-Nano / Qwen3-ASR
- 流式识别（Streaming ASR）：Paraformer-Streaming（chunk_size/cache/is_final/look_back）
- 说话人分离（Diarization）：ASR + VAD +（可选）PUNC + spk_model="cam++"，支持 spk_mode
- 情感识别（Emotion）：emotion2vec（或 SenseVoice 的情感标签）
- 语音活动检测（VAD）：离线与流式（返回区间 ms）
- 标点恢复（PUNC）：ct-punc（输入文本→输出带标点文本）

关键参数（来自官方示例，后续需要在 UI 中可配置）：

- 通用：model / device / hub(ms|hf) / disable_update / ncpu / log_level / disable_pbar
- ASR：batch_size_s / hotword(s) / language / use_itn / merge_vad / merge_length_s
- VAD：vad_kwargs.max_single_segment_time
- Streaming：chunk_size / encoder_chunk_look_back / decoder_chunk_look_back / cache / is_final
- Diarization：spk_model / spk_mode
- Emotion：granularity（utterance 等）

说明：当前项目的后端是 OpenAI-Compatible API（/v1/audio/transcriptions）。要覆盖以上全部能力，需要扩展后端 API 或让 WebUI 直接调用 FunASR 推理链路（二选一）。
