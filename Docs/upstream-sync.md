# FunASR 上游同步说明

## 目的

记录当前项目内置 FunASR 源码版本、官方参考入口，以及后续同步上游前必须完成的验证项。

## 当前版本

- 本地源码目录："app/funasr/"
- 本地版本文件："app/funasr/version.txt"
- 当前版本：`1.3.9`
- 当前策略：保留本地 vendored 源码，不在本专项中直接升级

## 官方参考

- 官方教程："https://modelscope.github.io/FunASR/zh/tutorial.html"
- 官方 API："https://modelscope.github.io/FunASR/api.html"
- 官方仓库："https://github.com/modelscope/FunASR"

## 同步原则

- 先记录官方变更，再决定是否同步源码
- 先跑静态配置与接口单测，再跑真实模型冒烟
- 不在未确认情况下下载模型、安装依赖或替换 vendored 源码
- 保持 OpenAI-Compatible API 与 Pat WebUI 的现有入口稳定

## 升级前验证清单

- [ ] 对比官方 `AutoModel` 参数变化，重点检查 `model`、`hub`、`trust_remote_code`、`dtype`
- [ ] 对比官方 `generate()` 参数变化，重点检查 `batch_size_s`、`batch_size_threshold_s`、`merge_vad`、`sentence_timestamp`
- [ ] 对比 SenseVoice、Paraformer、Fun-ASR-Nano、Qwen3-ASR 的模型 README
- [ ] 确认 `app/openai_api/server.py` 的 `MODEL_CONFIGS` 与文档一致
- [ ] 确认 `scripts/prefetch_models.py` 与 `scripts/batch_transcribe.py` 的模型配置与 API 一致
- [ ] 运行不触发下载的单元测试
- [ ] 用户确认后，再执行真实模型加载与音频冒烟

## 当前已知取舍

- SenseVoice / Fun-ASR-Nano / Qwen3-ASR 官方链路原生带标点，当前项目不默认给 SenseVoice 挂外置 `punc_model`
- Paraformer 系继续显式使用 `punc_model="ct-punc"` 提升可读性
- Qwen3-ASR 当前只接入离线路径，未接其原生 streaming / vLLM 工具链
- Fun-ASR-Nano 当前默认走 ModelScope 路径，未切换到官方教程中的 HuggingFace 示例配置
