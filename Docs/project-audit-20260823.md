# Pat-FunASR 项目优化、升级与问题审查报告

- 审查日期：2026-08-23
- 工作区：`Y:\NewStore\AI\pat-funasr`
- 分支：`main`
- 审查方式：离线静态审查、本地运行时元数据检查、语法检查、全量单元测试、最小化内存探针
- 边界：未联网、未安装/升级依赖、未构建 Docker、未调用真实模型或外部 LLM、未修改业务代码

## 一、结论摘要

当前本机 API 与 Pat WebUI 确实由本工作区的便携 Python 启动，并分别监听 `127.0.0.1:8000` 与 `127.0.0.1:7861`。SQLite 存储已正确使用 WAL、`synchronous=NORMAL` 和事务，Python 语法检查与项目 JSON 解析也通过。

但当前工作树不满足发布/升级验收条件：

- `1` 个 P0：Docker 镜像按现有 Dockerfile 构建后缺少 `server.py` 的本地依赖模块，无法正常启动。
- `8` 个 P1：测试回归失败、上传内存风险、异步路由阻塞/重复加载、错误状态码、长音频假流式及重复文本、LLM 熔断失效、思维导图脚本注入、容器默认暴露但缺少代码层防护。
- 多个 P2/P3：依赖不可复现、便携运行时残留损坏、精细转录缺少测试、临时文件无生命周期、Claude 示例不可用、FFmpeg 未真正便携、文档与版本漂移、无 CI/PR 模板、格式不统一和超大单文件。

建议先完成 P0/P1 修复和新增回归测试，再讨论 FunASR、PyTorch、Gradio 等版本升级；当前直接升级会把已有配置漂移和运行时不完整问题放大。

## 二、验证结果

| 检查 | 结果 |
|---|---|
| Python `compileall` | 通过 |
| `pytest tests -q` | `166 passed, 1 failed, 1 warning`，耗时 73.34 秒 |
| JSON 语法 | 3 个项目 JSON 全部通过 |
| SQLite | WAL、`synchronous=NORMAL`、外键与写事务均已实现 |
| 运行实例 | 8000/7861 均来自本工作区 `runtime\python\python.exe` |
| 明显密钥扫描 | 排除 `.env` 后未发现明显已提交私钥或 API Key |
| Docker 构建/启动 | 未执行；构建会联网安装依赖，尚未获批 |
| 真实 ASR/LLM 冒烟 | 未执行，避免模型下载和外部服务调用 |

## 三、必须优先处理的问题

### P0-01 Docker 镜像缺少本地模块，部署入口不可用

- 证据：`app/openai_api/Dockerfile:26` 只执行 `COPY server.py /app/server.py`；但 `app/openai_api/server.py:56-58` 在模块加载时立即导入 `renderers`、`vad_presets`、`segmentation`。
- 影响：镜像即使构建成功，启动时也会因本地模块不存在而报 `ModuleNotFoundError`。Docker/Compose/Kubernetes 文档描述的部署链路无法成立。
- 修复建议：至少复制上述模块及翻译语言模块；更稳妥的做法是把 API 作为一个明确的 Python 包复制并安装。添加“构建镜像 → 容器启动 → `/health`”的离线/缓存 CI 冒烟测试。
- 同时修正：Dockerfile 使用未固定版本的 `funasr fastapi uvicorn python-multipart`，且采用 Python 3.10，而本地便携运行时是 Python 3.11.9。应统一 Python 基线并使用锁定依赖。

### P1-01 模型配置已漂移，现有回归测试失败

- 复现：`tests/test_model_configs.py:84` 失败，当前结果为 `166 passed, 1 failed`。
- 证据：配置重复存在于 `app/openai_api/server.py:140`、`scripts/batch_transcribe.py:67` 和 `scripts/prefetch_models.py:22`。
- 已确认差异：
  - `sensevoice`、`paraformer`：API 为 `trust_remote_code=True`，批处理/预下载缺失。
  - `fun-asr-nano`、`qwen3-asr`：API/批处理为 `True`，预下载为 `False`。
  - `qwen3-asr`：API 有 `forced_aligner`，批处理和预下载缺失。
  - API 还包含流式、情感、翻译、0.6B 模型，另外两份配置没有对应能力。
- 影响：API、跑批和预下载可能获取不同模型代码、不同时间戳能力或直接加载失败。
- 修复建议：保留一份共享模型配置源，脚本只选择所需 alias；先决定每个模型的 `trust_remote_code` 安全策略，再修复测试并验证四个主模型。

### P1-02 上传限制在整文件读入内存后才生效

- 证据：`app/openai_api/server.py:1360-1363` 先 `await file.read()`，再检查 2GB 上限；情感和说话人分离端点在 `1626`、`1686` 直接读取且没有上限。流式端点也没有分片大小上限。
- 影响：单请求可以先占用接近 2GB 甚至更多内存，造成进程 OOM；并发上传时风险倍增。
- 修复建议：按固定块写入临时文件并累计字节数，超过可配置上限立即返回 413；所有音频端点复用同一上传函数，并给流式分片单独设置较小上限。

### P1-03 异步路由直接执行同步推理，模型加载锁也不能防止重复加载

- 证据：`server.py:1292/1527/1598/1661/1845/1873` 使用 `async def`，内部直接调用同步 `load_model()` 和 `.generate()`；`MODEL_LOAD_LOCK` 只在 `527-530` 检查缓存、在 `615-617` 写回缓存，耗时加载过程位于锁外。
- 影响：推理/加载会阻塞事件循环，长任务期间 `/health` 和状态查询可能无响应；两个并发首请求可同时加载同一大模型，占用双倍 CPU/GPU 内存。模型推理本身也没有按设备/模型串行或限流。
- 额外风险：`build_model_registry_key()` 把客户端可传入的 `device`、`hub`、`ncpu`、日志级别等组成永久缓存 key，注册表没有容量和淘汰策略；参数也缺少完整白名单和上界。
- 修复建议：同步重活放入受控 worker；同一模型使用 single-flight 加载；GPU 默认推理并发设为 1 或按实测配置；限制运行时覆写参数，设置注册表上限和显式卸载策略。

### P1-04 应返回 400 的参数错误被外层异常处理改成 500

- 证据：`server.py:1400-1401` 和 `1419-1420` 将 `ValueError` 转为 `HTTPException(400)`，但 `1521-1523` 又以 `except Exception` 捕获并包装为 500。
- 最小探针：mock `load_model()` 抛出 `ValueError` 后，实际响应为 `500 {'detail': '400: invalid runtime option'}`。
- 影响：客户端无法区分输入错误与服务故障，监控和重试策略会误判。
- 修复建议：在通用异常分支前增加 `except HTTPException: raise`，并增加端点级回归测试。

### P1-05 长音频 ASR 仍是假流式，且重叠内容会进入最终文本

- 假流式证据：`transcription_pipeline.py:469-486` 把每块事件先存入 `asr_partial_collector`，直到 `_call_asr_chunked()` 全部完成后才统一 `yield`。用户在长音频 ASR 阶段仍会长时间看不到进度。
- 重复文本证据：音频块重叠 10 秒，但 `280/289/303` 将各块原始文本直接 `join`；虽然 segments 做了有限去重，最终 `raw_text` 并未由去重后的 segments 重建。
- LLM 重复证据：`summary_processor.py:149-158` 生成带重叠的文本块，润色在 `381-387` 将每块完整输出直接拼接，没有边界消重；纪要和思维导图也会重复吸收重叠内容。
- 修复建议：让分块函数本身成为生成器并在每块完成时立即 yield；最终文本从去重后的 segments/words 重建；为重叠边界增加中英文重复句、时间戳抖动和无标点测试。

### P1-06 LLM 熔断使用请求开始时间，长超时后立即过期

- 证据：`summary_processor.py:54` 只在请求前取一次 `now`，`66-71` 在失败时仍用旧时间计算 `open_until`。
- 最小探针：模拟三次相隔 301 秒的 300 秒 ReadTimeout，第三次仍调用了 `requests.post`，`post_calls=3`，熔断状态又被重置为单次失败。
- 影响：最需要熔断的慢超时场景仍会继续等待，原先“连续两次失败后快速跳过”的修复目标没有达成。
- 修复建议：失败时重新读取单调时钟 `time.monotonic()`；状态读写加锁；补充“快速拒绝”和“完整读超时”两类测试。

### P1-07 思维导图存在脚本注入边界，并引入未固定 CDN 依赖

- 证据：`audio_sync_js.py:237` 只替换小写精确 `</script>`；`257-259` 从 jsDelivr 加载脚本；`285` 将 LLM 节点标题拼成 HTML；`313-315` 通过 `innerHTML` 写入；`332-334` 明确不设置 iframe sandbox。
- 最小探针：大小写变体 `</ScRiPt>` 仍保留在浏览器解码后的 `srcdoc` 中，且确认 `innerHTML_used=True`、`sandbox_present=False`。
- 影响：恶意音频内容或异常 LLM 输出可能形成同源脚本执行；同时“全部本地、隐私安全”的产品描述与 CDN 请求不一致。
- 修复建议：不要把数据拼入脚本或 `innerHTML`；使用 DOM `textContent` 构建降级树，JSON 用安全数据通道传递；恢复 sandbox，并把 d3/markmap 静态资源固定版本后本地化。

### P1-08 容器默认暴露到宿主机所有网卡，但 API 无代码层鉴权和限流

- 证据：Docker CMD 使用 `--host 0.0.0.0`，Compose 映射 `${FUNASR_HOST_PORT:-8000}:8000`；API 包含主动模型加载、超大上传和运行参数覆写端点。
- 影响：若宿主防火墙或云安全组允许访问，局域网/公网客户端可消耗 GPU、触发模型加载和大文件内存压力。
- 修复建议：Compose 默认绑定 `127.0.0.1`；跨主机部署必须通过已配置的网关鉴权、TLS、上传限制和限流。仅有安全文档说明不能替代默认安全配置。

## 四、建议优化与升级项

### P2-01 建立可复现的便携运行时清单

- 仓库没有 `pyproject.toml`、requirements 锁文件或运行时 manifest。
- 本地 `pip list` 报告无效发行包残留：`~-ggingface_hub-0.33.5.dist-info`、`~~ggingface_hub`、`~uggingface_hub`、`~uggingface_hub-1.17.0.dist-info`；实际可导入版本为 `huggingface_hub 0.36.2`。
- `import funasr` 本机耗时约 17.651 秒，并静默记录 33 个导入错误（28 个缺模块、5 个导入错误），包括 `websockets`、`pytorch_wpe`、`sklearn`、`cn_tn`、`whisper` 等。
- 建议：不要手工删除单个残留目录后继续沿用；从一份固定 Python/CUDA 组合重新生成便携运行时，输出完整版本清单、来源、hash 和核心能力导入测试。

### P2-02 依赖升级顺序，而不是直接全量升级

当前本地关键版本如下；“是否为最新”尚未联网核验：

| 组件 | 当前值 | 建议 |
|---|---:|---|
| Python | 3.11.9 | 与 Docker 基线统一后再升级 |
| vendored FunASR | 1.4.1 | 文档先改正，再做上游 commit/tag 对比 |
| PyTorch / torchaudio | 2.5.1+cu121 | 与显卡驱动、CUDA 和模型矩阵整体验证 |
| Gradio | 6.15.2 | 先补精细转录 UI/HTML 回归测试 |
| FastAPI / Starlette / httpx | 0.136.3 / 1.2.1 / 0.28.1 | 先消除 TestClient 弃用警告并锁定兼容组合 |
| Transformers | 4.57.6 | 与 NLLB、Qwen3-ASR 单独冒烟 |
| ModelScope | 1.37.1 | 与缓存布局和 `trust_remote_code` 策略一起验证 |
| pytest | 9.1.1 | 可保留，先修复当前失败测试 |

### P2-03 精细转录核心缺少测试

- `transcription_pipeline.py`、`summary_processor.py`、`audio_sync_js.py` 共 1,590 行，但 `tests/` 中没有对这些模块的直接引用。
- 应先补：分块实时事件、重叠消重、文件句柄关闭、熔断计时/并发、JSON schema、HTML 注入、CDN 离线降级、SQLite 部分失败一致性测试。

### P2-04 临时文件和文件句柄缺少生命周期

- `_call_asr()` 在 `transcription_pipeline.py:200` 直接 `open()` 音频但不显式关闭。
- WebUI 多处向 `%TEMP%` 写日志包、字幕、批处理 ZIP、精细转录导出和预处理 WAV，没有清理策略。
- 建议：请求内文件使用上下文管理器；下载产物使用带 TTL 的项目临时目录，启动时/定时清理过期文件，并保留正在被 Gradio 下载的文件。

### P2-05 `.env.sample` 的 Claude 示例不可用

- 示例把 provider 设为 `claude`、地址设为 `https://api.anthropic.com/v1`，但 `summary_processor.py:83-102` 对所有 provider 都发送 OpenAI `/chat/completions`、Bearer header 和 OpenAI body；`provider` 字段只读取、不参与协议分派。
- 建议：若只支持 OpenAI-compatible API，就删除原生 Anthropic 示例并明确要求兼容代理；若要支持 Claude，新增独立适配器和测试。

### P2-06 FFmpeg 不是便携包的一部分

- `audio_processor.py:23-24` 默认硬编码 `C:\ffmpeg\bin\ffmpeg.exe`；当前机器恰好存在该路径，但 `runtime/` 未发现 `ffmpeg.exe/ffprobe.exe`，README/Requirements 也没有把它列为外部前置条件。
- 建议：把 FFmpeg 放入受控的 `runtime/ffmpeg/` 并统一解析路径，或明确要求安装并在启动时检查；`transcription_pipeline.py` 与 `audio_processor.py` 应使用同一个解析函数。

### P2-07 文档、入口和格式已经漂移

- `app/funasr/version.txt` 为 `1.4.1`，`Docs/upstream-sync.md:11` 和升级模板仍写 `1.3.9`。
- 根 README 指向不存在的 `Docs/optimization-plan.md`；模板还引用被 `.gitignore` 排除且当前不存在的 `Whisper-CTranslate2` 文件。
- `start_services.py` 仍启动旧 `app/openai_api/gradio_app.py` 和端口 7860，而正式入口已是 Pat WebUI 7861；文档仍把它列作启动器。
- 非 vendored 审查范围内有 53 个文本文件含 LF，30 个 Markdown 没有 UTF-8 BOM，不符合当前项目规则。
- 建议：明确删除/归档旧入口，修正文档与链接；增加 `.gitattributes`/格式校验，单独批次做机械换行和 BOM 规范化，避免与当前功能改动混合。

### P2-08 缺少 CI、PR 模板与静态质量门禁

- 没有 `.github/`、PR 模板、lint/type-check 配置或依赖锁文件。
- 建议最低门禁：`compileall`、pytest、模型配置一致性、Markdown 链接、JSON/YAML 解析、格式/BOM、Docker import/health smoke；之后再逐步加入 Ruff 和类型检查。

### P2-09 超大单文件提高回归概率

- `app/pat_funasr_webui/gradio_app.py` 4,387 行；`app/openai_api/server.py` 1,973 行；精细转录管线 836 行。
- 建议按现有功能边界做有限拆分：API routers + model service + upload service；WebUI 每个 Tab 单独模块，共享状态/导出工具保持一份。先补测试再移动代码，不做行为改写式重构。

### P3-01 小型可维护性问题

- `tests/test_aipython_managed_single_window_launcher.py:40` 使用旧项目名的硬编码绝对路径，虽然当前测试通过，但不可移植。
- `aipython/tsv_to_srt.py:31` 默认输入也是当前机器绝对路径。
- 部分导出文本使用 `encoding="utf-8"`，部分使用 `utf-8-sig`，与项目格式规则不一致。

## 五、推荐执行顺序

1. 修复 Docker 文件复制和依赖锁定，建立可启动的容器基线。
2. 统一模型配置源，恢复全量测试为 0 失败。
3. 修复上传流式写入、400/500 状态码、模型 single-flight、推理限流和容器默认绑定。
4. 修复精细转录假流式、重叠重复、LLM 熔断和文件句柄；同时补核心测试。
5. 移除思维导图注入路径并本地化静态资源。
6. 重建便携运行时，统一 Python/依赖版本并清除无效发行包残留。
7. 清理旧入口、断链、版本文档和文本格式；增加 CI/PR 门禁。
8. 获批联网后，再核对 FunASR、PyTorch、Gradio、FastAPI/Starlette/httpx、Transformers、ModelScope 的最新稳定版与安全公告，按组件逐项升级，不做一次性全量升级。

## 六、修复后的验收标准

- `compileall` 与全量 pytest 均为 0 失败、0 非预期警告。
- Docker 镜像可构建，容器无需额外复制文件即可启动并通过 `/health`。
- 超限上传在内存稳定的情况下返回 413；所有音频端点使用同一限制策略。
- 两个并发首请求只加载一次模型；长推理期间 `/health` 仍能及时响应。
- 长音频在每块结束后立即产生 UI 事件，最终文本无重叠重复。
- 两次完整 ReadTimeout 后，第三次 LLM 调用在 5 分钟窗口内不发请求。
- 包含 HTML/script 字符串的思维导图标题只能显示为文本，不能执行脚本。
- API 输入错误保持 4xx，内部错误不向客户端泄漏不必要的实现细节。
- 便携运行时无无效 distribution 警告，核心能力导入错误有明确允许列表。
- 文档链接、版本号、UTF-8 BOM/CRLF 和 CI/PR 检查全部通过。

## 七、本轮未执行事项

- 未修改上述业务问题；本报告是诊断和修复排序，不是修复提交。
- 未联网核对最新版本或安全公告。
- 未安装、删除或升级依赖，未清理运行时残留目录。
- 未构建 Docker，未请求当前 API，未运行真实音频、模型或 LLM 冒烟。
- 未 commit、push、pull、merge 或部署。

