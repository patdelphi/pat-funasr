# 精细转录模块融入 pat-funasr 计划（修订版）

> 策略 C 修订：吸收 OddMinutes 的**音字联动**能力，新增**音频前处理**和**精细转录**两大模块，替代原计划的双轨录音和会议纪要。

## 一、修订说明

| 原计划 | 修订后 | 原因 |
|---|---|---|
| 双轨录音 + AGC | **音频前处理**（降噪、提升分辨率） | 不需要双轨采集，改为提升输入音频质量 |
| 音字联动 | **音字联动**（保留） | 核心交互能力，不变 |
| 会议纪要 | **精细转录**（场景化 ASR+LLM 协同） | 从简单摘要升级为场景化精细转录管线 |

## 二、三大模块设计

### 模块 1：音频前处理（替代双轨录音）

**目标**：对输入音频做降噪、重采样、VAD 静音裁剪，提升 ASR 输入质量。

**功能清单**：
1. **降噪**：用 ffmpeg `afftdn`（频域降噪）或 `anlmdn`（非局部均值降噪）去除背景噪声
   - 可选 `noisereduce` 库做 Python 级频谱降噪（对白噪声效果极佳）
   - 参数可调：降噪强度（noise_reduction dB）、灵敏度
2. **提升采样率/位深**：ffmpeg 重采样到 16kHz（ASR 标准）或 48kHz（高保真）
   - 位深度提升：16-bit → 24-bit/32-bit float（减少量化噪声）
   - 声道处理：立体声→单声道（ASR 友好）或保留多声道做说话人分离
3. **VAD 预处理**：用现有 `fsmn-vad` 模型裁剪静音段，减少 ASR 处理时间
4. **音量归一化**：`loudnorm` 或 `dynaudnorm` 统一音量电平

**实现方式**：
- 新建 `app/pat_funasr_webui/fine_transcription/audio_processor.py`
- 核心用 `ffmpeg-python` 或直接调 `subprocess` 调 ffmpeg
- 不引入额外重型依赖（noisereduce 可选，ffmpeg 已有）

**Gradio UI**：
- 上传音频文件
- 前处理参数面板：降噪强度滑块、采样率选择、VAD 开关、音量归一化开关
- 处理后音频预览（可对比原始音频）
- 输出 WAV 文件，自动送入 ASR 转写

**验证标准**：
- 带背景噪声的音频降噪后 SNR 明显提升
- 16kHz 输出与原始 44.1kHz 输出的 ASR 结果对比，降噪版错字率降低
- VAD 裁剪后音频时长缩短，但无截断

---

### 模块 2：音字联动（保留原设计）

**目标**：转写结果与音频播放器联动——点击文字跳转音频，播放时高亮当前文字。

**核心实现**（从 OddMinutes `app.js` 移植，纯前端 JS）：
1. `highlightWordAtTime(currentTime)` — 遍历 `transcript-word[data-start]` span，找到 `start <= t <= end` 的高亮
2. `scrollToWordAtTime(currentTime)` — 同上逻辑 + `scrollIntoView({behavior:'smooth'})`
3. `seekAudio(time)` — 点击文字 → `audio.currentTime = time` + `audio.play()`
4. 事件绑定：`audio.timeupdate` → 高亮；`audio.seeked` → 滚动

**数据结构**：每个字/词渲染为
```html
<span class="transcript-word" data-start="1.23" data-end="1.56">腾不出</span>
```
时间戳来源：ASR `verbose_json` + `timestamp_granularities=word`

**验证标准**：
- 点击文字 → 音频跳转到对应时间并播放
- 音频播放时 → 当前字高亮 + 自动滚动
- 拖动进度条 → 文字自动滚动到对应位置

---

### 模块 3：精细转录（替代会议纪要）

**目标**：用户选择场景 → 基于预设模板和 prompt → 调用 ASR + LLM 做精细转录 → 多种产出。

**核心流程**：
```
场景选择 → 加载预设模板(词表+prompt) → 音频前处理 → ASR 转写(hotword 热词增强)
    → 说话人分离 → LLM 二次优化(纠错/润色/分段) → 提炼纪要/思维导图 → 导出
```

**场景预设模板**（`scene_templates.py`）：

| 场景 | 专业词表 | ASR 参数 | LLM prompt 要点 |
|---|---|---|---|
| 精细会议转录 | 职位、部门、项目名、人名 | hotword=词表, use_itn=true, diarization=true | 识别说话人，按发言人分段，纠错专业术语，提炼纪要(7字段) |
| 访谈/调研 | 行业术语、产品名 | hotword=词表, use_itn=true | Q&A 格式整理，区分访问者和受访者 |
| 讲座/课程 | 学科术语、公式、人名 | hotword=词表, use_itn=true | 按知识点分段，生成学习笔记+思维导图 |
| 法庭/取证 | 法律术语 | hotword=词表, use_itn=true | 逐字稿+摘要，标注关键时间点 |
| 医疗/问诊 | 医学术语、药品名 | hotword=词表, use_itn=true | SOAP 格式整理，主诉/诊断/处方 |
| 通用转录 | 无 | use_itn=true | 仅转写+分段，不做摘要 |

**每个场景的模板结构**：
```python
@dataclass
class SceneTemplate:
    scene_id: str           # "meeting", "interview", "lecture" ...
    name: str               # "精细会议转录"
    description: str        # 场景说明
    hotwords: list[str]     # 专业词表
    asr_params: dict        # ASR 参数覆盖(hotword, use_itn, diarization, vad_preset...)
    llm_prompt: str        # LLM 二次优化 prompt
    summary_prompt: str     # 纪要提炼 prompt
    mindmap_prompt: str     # 思维导图提炼 prompt
    output_formats: list[str]  # ["transcript", "summary", "mindmap"]
```

**LLM 二次优化管线**（`transcription_pipeline.py`）：
1. **ASR 转写**：调用现有 `/v1/audio/transcriptions`，传入场景的 hotword 词表
2. **说话人分离**：调用现有 diarization 能力（camplus 模型），给每个 segment 标注 speaker
3. **LLM 纠错+润色**：将 ASR 原始文本 + 专业词表送 LLM，prompt 指示：
   - 根据词表纠正同音错字
   - 按语义重新分段（合并碎片句子）
   - 添加标点（如果 ASR 未加）
   - 保持说话人标注不丢失
4. **提炼纪要**：对润色后文本用场景特定的 summary_prompt 生成结构化 JSON
5. **提炼思维导图**：用 mindmap_prompt 让 LLM 输出 JSON 树结构（根节点→分支→叶子），前端用 markmap 或 mermaid 渲染

**LLM 调用方式**：
- 用 `openai` 库统一接口，兼容 Ollama（`base_url=http://127.0.0.1:11434/v1`）/OpenAI/Claude
- chunk 分段处理（5000 字 + 1000 overlap），`aggregate_summaries()` 聚合
- 超时 300s，异常重试 1 次

**SQLite 存储**（`store.py`）：
- 表结构简化自 OddMinutes，WAL 模式
- 存储：任务元数据 + ASR 原始 segments + LLM 润色后文本 + 纪要 JSON + 思维导图 JSON

**Gradio UI**（新增 "精细转录" Tab）：
1. **场景选择**：下拉选择场景（自动加载对应模板）
2. **词表编辑**：可查看/编辑当前场景的专业词表（textarea，每行一个词）
3. **音频上传**：支持文件上传或从录音模块传入
4. **前处理**：降噪/重采样/VAD 参数面板
5. **执行按钮**："开始精细转录"（串联 前处理→ASR→说话人→LLM优化→纪要→思维导图）
6. **结果展示**：
   - 转写文本（带说话人标注 + 音字联动）
   - 会议纪要（7 字段结构化展示）
   - 思维导图（markmap 嵌入渲染）
7. **导出**：txt / md / json / mindmap(mmap)

**验证标准**：
- 专业词表的 hotword 传入 ASR 后，专业术语识别准确率提升
- LLM 二次优化后，同音错字减少、分段合理
- 纪要 7 字段 JSON 结构完整
- 思维导图 JSON 可被 markmap 渲染为树状图
- 导出 4 种格式内容完整

## 三、文件规划

```
app/pat_funasr_webui/
├── gradio_app.py                  # 新增 "精细转录" Tab
├── fine_transcription/            # 新增模块
│   ├── __init__.py
│   ├── audio_processor.py         # 音频前处理：ffmpeg 降噪/重采样/VAD
│   ├── audio_sync_js.py           # 音字联动前端 JS（从 OddMinutes 移植）
│   ├── scene_templates.py         # 场景预设模板：词表+prompt+ASR参数
│   ├── transcription_pipeline.py  # ASR+LLM 协同管线
│   ├── summary_processor.py       # 纪要/思维导图 LLM 生成
│   └── store.py                   # SQLite 存储（WAL 模式）
└── ...
```

## 四、依赖

| 包 | 用途 | 是否已安装 |
|---|---|---|
| ffmpeg | 降噪/重采样/VAD | ✅ 已有（C:\ffmpeg\bin\ffmpeg.exe） |
| openai | LLM 统一调用 | ❌ 需安装（或用 requests） |
| numpy | 音频处理 | ✅ |
| sqlite3 | 数据存储 | ✅（Python 内置） |
| markmap | 思维导图渲染 | 前端 CDN 引入，无需安装 |

> 不安装 Django、pydantic-ai、sounddevice、noisereduce（可选）

## 五、Tab 布局决策（方案 C）

**用户选定：2 个新 Tab，共 8 个 Tab。**

```
[离线识别] [流式识别] [说话人分离] [情感识别] [跨语言翻译] [音频工具] [精细转录] [服务与调试]
```

- **音频工具 Tab**：独立前处理（降噪/重采样/VAD/音量归一化），输出 WAV 可下载或直接送入精细转录
- **精细转录 Tab**：完整管线（场景选择 → 前处理[可选] → ASR → 说话人 → LLM优化 → 纪要 → 思维导图），内嵌音字联动
- 前处理逻辑共享 `audio_processor.py` 函数，避免代码重复

## 六、分阶段实施

1. 阶段 1：音频工具 Tab（前处理模块）→ 验证降噪效果
2. 阶段 2：精细转录 Tab（场景模板 + ASR + LLM 管线 + 音字联动 + 纪要 + 思维导图）→ 验证全链路
3. 全链路联调 → 验证完整流程
4. commit（不 push）

> 每阶段完成后等待用户确认再进入下一阶段。
