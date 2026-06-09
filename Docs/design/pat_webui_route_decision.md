﻿﻿﻿﻿﻿# Pat WebUI 技术路线决策（C1）

## 结论（本项目采用）

- 采用路线：**扩展后端 API**，Pat WebUI 继续通过 HTTP 调用后端，不在前端直连 FunASR 推理链路
- 保留兼容：继续保留现有 OpenAI-Compatible API（`/v1/models`、`/v1/audio/transcriptions`），作为基础 ASR 通路
- 扩展新增：为“功能完整 WebUI”补齐 FunASR 官方能力，新增 `"/v1/funasr/*"` 系列接口

## 为什么不让 WebUI 直连 FunASR

- 前端直连会把模型加载、缓存、设备选择、并发控制等复杂度推到 UI 进程，排错与复用成本更高
- 后端统一控制 `hub="ms"`、`disable_update=True`、设备策略（cpu/cuda）更符合现有项目约束
- 后端 API 更容易做权限、限流、日志、异常格式化与测试（UI 只负责交互与展示）

## 新增接口草案（对齐官方教程/API）

说明：这些接口是增强能力入口，**不替代** `/v1/audio/transcriptions`，避免破坏兼容性。

- `POST /v1/funasr/asr`
  - 用途：离线识别（可覆盖 batch_size_s / hotword / language / use_itn / merge_vad / merge_length_s）
- `POST /v1/funasr/streaming`
  - 用途：流式识别（覆盖 chunk_size / encoder_chunk_look_back / decoder_chunk_look_back / cache / is_final）
  - 形态建议：先做 **HTTP 分片/多轮请求（带 cache）**，后续再评估 WebSocket
- `POST /v1/funasr/diarization`
  - 用途：说话人分离（覆盖 spk_model / spk_mode）
- `POST /v1/funasr/emotion`
  - 用途：情感识别（覆盖 granularity）
- `POST /v1/funasr/vad`
  - 用途：VAD（覆盖 vad_kwargs.max_single_segment_time）
- `POST /v1/funasr/punc`
  - 用途：标点恢复（Paraformer 系需要 punc_model="ct-punc" 的显式策略）

## 与 Pat WebUI 的对应改动

- Pat WebUI 增加 Tab：
  - ASR（继续走 `/v1/audio/transcriptions`，维持 MVP 稳定）
  - Streaming / Diarization / Emotion / VAD / PUNC（走 `/v1/funasr/*`）
- 所有请求参数仍走白名单拼装，禁止任意字段透传

## 测试与验收策略（先测试后实现）

- 单元测试：
  - 请求字段白名单与参数映射（不加载模型）
  - 新增接口的参数校验与错误码（不加载模型）
- 集成回归：
  - 复用 `aipython/asr_b5_regression.py` 思路，为 `/v1/funasr/*` 增加最小 smoke 脚本

## 后续执行顺序（C2）

- 优先级 1：Streaming（交互价值最高）
- 优先级 2：Diarization（对会议/访谈场景关键）
- 优先级 3：Emotion（展示增强能力）
- 优先级 4：VAD / PUNC（作为参数与效果增强的补齐项）
