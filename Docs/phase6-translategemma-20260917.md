# 阶段 6 变更摘要：TranslateGemma 集成（2026-09-17）

> 范围：新增可选翻译模型 `google/translategemma-4b-it`（Gemma3 decoder-only，chat template 驱动），与 NLLB 共存。
> 用户确认：新增可选模型（NLLB 保持默认）、本地优先/联网下载、优先 GPU。

## 改动文件

| 文件 | 内容 |
|------|------|
| app/model_catalog.py | MODEL_CONFIGS 新增 `translategemma-4b-it`（model=google/translategemma-4b-it，hub=hf，type=translation）；MODEL_CAPABILITIES 新增条目（translation=True，注释标注语言码为 ISO 639-1） |
| app/openai_api/server.py | load_model translation 分支按模型分派：`translategemma` 走新逻辑，其余（NLLB）原样；新增内嵌 `TranslateGemmaTranslationModel` 类 |
| app/pat_funasr_webui/gradio_app.py | 精细转录 tab 与跨语言翻译 tab 的翻译模型下拉各增加 "TranslateGemma-4B-IT" |
| tests/test_server_translategemma.py | 新增 7 个测试用例（见下） |

## TranslateGemmaTranslationModel 设计

- 加载：`AutoProcessor` + `AutoModelForImageTextToText`（与 NLLB 的 `AutoTokenizer` + `AutoModelForSeq2SeqLM` 不同）
- 本地缓存存在（`model_path` 命中）→ `local_files_only=True` 禁止联网；否则允许联网下载（HF gated 模型，需 token）
- GPU 优先：`device` 非 cpu 时加载到指定设备，CUDA 不可用/显存不足自动回退 CPU 并记 warning
- 语言码：FLORES-200 → ISO 639-1 映射（zho_Hans→zh、zho_Hant→zh-Hant、eng_Latn→en、jpn_Jpan→ja、kor_Kore→ko、fra_Latn→fr、tha_Thai→th、zsm_Latn→ms、vie_Latn→vi）；未知码抛 ValueError
- 输入：`apply_chat_template` 构造 user 消息（content 含 type/source_lang_code/target_lang_code/text）
- 生成：`generate(do_sample=False, max_new_tokens=max(max_length, 1024))`；num_beams 对 decoder-only 无效，忽略
- 分块：≤1200 字/块（Gemma 上下文 2K tokens），按换行/句号/感叹号/问号切分，逐块翻译后拼接

## 端点兼容

- `/v1/translations` 零改动：num_beams 忽略与 max_length→max_new_tokens 均在模型类内处理；语言码校验仍走 TRANSLATION_LANGUAGES_MAP（FLORES 集）

## 测试（7 个，全部通过）

1. 注册进 MODEL_CONFIGS / MODEL_CAPABILITIES
2. 加载走 AutoProcessor + AutoModelForImageTextToText，NLLB 分支不被调用
3. 本地无缓存时 `local_files_only=False`（允许联网下载）
4. 语言码映射（zho_Hans→zh / eng_Latn→en）+ chat template 消息结构 + generate 参数
5. 未知语言码抛 ValueError
6. 1500 字长文本切成 2 块并拼接
7. CUDA 失败回退 CPU

## 验证结果

- 全量回归：`python -m pytest -q` → **286 passed**（阶段 5 收尾 279 + 本次新增 7）
- NLLB 路径不受影响（加载分支按 model_id 分派）

## 未执行/注意事项

- 未提交 git（需确认）
- TranslateGemma 为 gated 模型，首次加载需 HF token；当前环境无法连接 huggingface.co，联网下载会失败，需提供代理/token 或手动放置本地缓存
- zho_Hant→zh-Hant 为非标准组合，需真实模型实测确认

---

## GGUF 量化版集成（同日追加，用户确认按 GGUF 方案执行）

> 背景：`google/translategemma-4b-it` 为 gated 模型（manual），无 token 无法下载（hf.co 与 hf-mirror.com 均 401）。
> 改用社区量化仓库 `mradermacher/translategemma-4b-it-GGUF`（免 token），选 **Q4_K_M**（2.6GB），llama.cpp 推理。

### 方案取舍

| 项 | 说明 |
|----|------|
| 模型仓库 | mradermacher/translategemma-4b-it-GGUF（static quants of google/translategemma-4b-it，Gemma3 架构） |
| 量化档位 | Q4_K_M（fast, recommended；官方表格标注 2.6GB） |
| 推理框架 | llama-cpp-python（非 transformers），手动拼 Gemma3 prompt（`<start_of_turn>`） |
| 语言码 | 同上 FLORES→ISO 639-1 映射，prompt 用 ISO 码 |
| 分块 | ≤1200 字/块（与 transformers 版一致），max_tokens=max(max_length, 1024) |
| GPU | n_gpu_layers=-1 全量 offload；加载失败自动回退 n_gpu_layers=0（CPU） |

### 环境适配（本机 RTX 3080 10GB / Ryzen 5800X 无 AVX-512）

- llama-cpp-python 安装：cu121/cu122 索引最高 0.3.4（不支持 Gemma3 架构）；cu124 索引选 **0.3.30**（0.3.35 的 cu124 wheel 在本机报 `0xc000001d` 非法指令，5800X 无 AVX-512）
- Q4_K_M 已下载至 HF 全局缓存 `C:\Users\patde\.cache\huggingface\hub\models--mradermacher--translategemma-4b-it-GGUF\snapshots\35a7486e...\translategemma-4b-it.Q4_K_M.gguf`
- 实测：GPU 加载 2.7s、推理 50.6 tok/s；真实加载冒烟测试翻译正确

### 代码改动

| 文件 | 内容 |
|------|------|
| app/model_catalog.py | MODEL_CONFIGS 新增 `translategemma-4b-it-gguf`（model=mradermacher/translategemma-4b-it-GGUF，hub=hf，type=translation，format=gguf）；MODEL_CAPABILITIES 新增条目；**修复 `_has_model_payload`**：GGUF 仓库仅含 .gguf 文件、无 config.json，此前 HF 快照布局解析返回 None，现识别 .gguf 即视为有效 |
| app/openai_api/server.py | load_model translation 分支新增 `format == "gguf"` 分支：缓存目录内定位 .gguf（排除 .mmproj），`llama_cpp.Llama(model_path, n_ctx=2048, n_gpu_layers=-1)` 优先 GPU、失败回退 CPU；内嵌 `TranslateGemmaGgufTranslationModel`（FLORES→ISO 映射、Gemma3 prompt 手动拼接、1200 字分块）；transformers 版分支改走 `elif "translategemma" in model_id` |
| app/pat_funasr_webui/gradio_app.py | 精细转录 tab 与跨语言翻译 tab 的翻译模型下拉各增加 "TranslateGemma-4B-IT (GGUF)" |
| tests/test_server_translategemma_gguf.py | 新增 7 个用例（mock llama_cpp，隔离真实推理） |
| tests/test_model_configs.py | 新增 HF 快照布局解析测试（models--Org--Name/snapshots/<hash>/.gguf 命中） |

### GGUF 测试（7 个，全部通过）

1. 注册进 MODEL_CONFIGS / MODEL_CAPABILITIES（format=gguf）
2. 加载走 llama_cpp.Llama 且 n_gpu_layers=-1（GPU 全量），选中 Q4_K_M 而非 mmproj
3. prompt 构造：`Translate the following text from zh to en`、`<start_of_turn>` 前缀、stop=["<end_of_turn>"]、temperature=0.0、max_tokens≥1024
4. 未知语言码抛 ValueError
5. 1500 字长文本切成 2 块并拼接
6. GPU 加载失败回退 CPU（n_gpu_layers==0）
7. 缓存目录无 .gguf 时抛 FileNotFoundError

### 验证结果

- 真实加载冒烟测试：`server.load_model('translategemma-4b-it-gguf', device='cuda')` → 加载 2.7s（GPU），翻译 0.5s，输出 "The weather is great today, let's go for a walk in the park together."
- 全量回归：**293 passed**（286 + 新增 GGUF 相关 7）；模型配置与 GGUF 集成测试 16 passed
- llama-cpp-python 进程退出时的 `Llama.__del__` TypeError 为库自身清理噪音，不影响功能

### 未执行/注意事项

- 未提交 git（需确认）
- 首次运行需缓存目录已有 .gguf（当前已就绪）；若缓存缺失，llama-cpp-python 无联网下载能力，需手动放置

---

## 显存优化与缓存/UI 适配（同日追加）

### 显存优化（用户报 NLLB 600M 卡慢后确认 A+B）

| 项 | 内容 |
|----|------|
| 诊断 | RTX 3080 10GB 显存 9870/10240 MiB 爆满——NLLB 1.3B(fp32 5.1GB)+600M(fp32 2.3GB)+GGUF(2.6GB)+FunASR 同时驻留（idle TTL 30min），600M 推理几乎 OOM 极慢 |
| A | NLLB GPU 加载 `.half().to(device)` fp16（600M 省 1.15GB、1.3B 省 2.55GB） |
| B | 切换翻译模型自动卸载其他翻译模型（GGUF 调 llama.close()、transformers 释放权重 + empty_cache） |
| 500 修复 | NLLB GPU 加载失败（CUDA 上下文异常）回退 CPU，不再 500 |
| 502 修复 | 根因 llama.cpp 显存不足 C++ 层 abort() 杀死进程（网关 502，Python 无法捕获）；GGUF 加载前查显存余量，<4GB 先卸载 FunASR 腾显存重试、仍不足才回退 CPU |
| 并发保护 | 切换翻译模型只卸载翻译模型（FunASR 保留，不打断并发转录）；仅 GGUF 缺显存时才卸载 FunASR（ASR 下次自动重载） |

### 缓存查找与 UI 适配（用户确认）

1. **查找顺序**：本机共享缓存优先（ModelScope 全局缓存 → HuggingFace 全局缓存），项目 `workspace/models` 兜底（`_model_cache_roots()`）
2. **加载自适应**：`resolve_local_model_path` 兼容 ModelScope 布局（Org/Name、Org___Name）与 HF 布局（models--Org--Name/snapshots/<hash>/）
3. **下载策略**：语音模型 FunASR 内部下载优先 ms（静态 hub=ms）；翻译 GGUF 仅存在于 HF，本地未命中自动 `snapshot_download` 到 HF 共享缓存后加载；下载失败抛明确 FileNotFoundError
4. **UI**：精细转录 tab 与跨语言翻译 tab 翻译模型下拉**只保留 "TranslateGemma-4B-IT (GGUF)"**（移除 NLLB 600M/1.3B 与非 GGUF gemma3）；NLLB/gemma3 注册与 API 保留，端点仍可用

### 相关测试

- test_server_translategemma_gguf.py：新增 GGUF 自动下载 / 下载失败 / 缺文件报错（下载也失败时）用例
- test_model_configs.py：新增 `test_model_cache_roots_prefer_shared_cache`（共享缓存优先顺序断言）
- 相关测试 21 passed；全量回归后台确认
