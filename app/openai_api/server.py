"""
FunASR OpenAI-Compatible API Server

Drop-in replacement for OpenAI's /v1/audio/transcriptions endpoint.
Works with any agent framework that supports OpenAI audio API.

Usage:
    python server.py --device cuda --port 8000

Then use with any OpenAI-compatible client:
    curl http://localhost:8000/v1/audio/transcriptions \
      -F file=@audio.wav -F model=sensevoice
"""

from pathlib import Path
import sys

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
_APP_DIR = _THIS_DIR.parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))
_PROJECT_ROOT = _APP_DIR.parent  # 项目根目录

# 绕过旧版 PyTorch 下 CVE-2025-32434 的安全警告限制，直接空置该检查函数
try:
    import transformers.utils.import_utils
    transformers.utils.import_utils.check_torch_load_is_safe = lambda: None
except Exception:
    pass

try:
    import transformers.modeling_utils
    transformers.modeling_utils.check_torch_load_is_safe = lambda: None
except Exception:
    pass

import argparse
from datetime import datetime
from functools import partial
import copy
import tempfile
import time
import os
import re
import asyncio
import gc
import logging
import json
import shutil
from typing import Optional
import threading
import uuid
import urllib.request

import numpy as np
import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

import renderers
import vad_presets
import segmentation
import media_service
import workflow_service
import workflow_runner
import artifact_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class _PadTokenIdFilter(logging.Filter):
    """抑制 transformers 反复打印 pad_token_id 警告。"""
    def filter(self, record: logging.LogRecord) -> bool:
        return "pad_token_id" not in record.getMessage()


# 过滤所有 logger 中的 pad_token_id 警告
logging.getLogger().addFilter(_PadTokenIdFilter())


# ============ Summary / Mindmap Prompt 常量 ============
# 核心原则：LLM 本身能力足够，prompt 只需要：
# 1. 明确输入格式（转写带 spk=N 标签）
# 2. 明确输出 JSON schema
# 3. 不要瞎编 participants（从 spk=N 推断）

_SUMMARY_INPUT_HINT = """输入格式说明：
- 开头有"=== 说话人统计 ==="，列出每个 spk=N 的发言比例和首句
- 转写每行格式：[MM:SS] [spk=N] 文本内容
- participants 必须从 spk=N 推断角色，**不要瞎编真实人名**"""

_SUMMARY_SCHEMA = """返回 JSON 结构（字段名必须完全匹配）：
{
  "meeting_title": "一句话会议主题",
  "participants": [{"spk": "spk=N", "role": "角色推断，不确定则留空"}],
  "overall_summary": "3-5 句总览：会议目的、核心结论、最重要的决定",
  "sections": [
    {
      "topic": "议题名称",
      "summary": "本议题的讨论内容和结论，2-5 句，提炼语气",
      "key_points": ["关键要点"],
      "quotes": [{"speaker": "spk=N", "timestamp": "MM:SS", "text": "原话引用"}],
      "decisions": ["明确达成的决定"],
      "action_items": [{"task": "具体任务", "owner": "角色或 spk=N", "deadline": "时间"}],
      "risks_or_todos": ["风险/分歧/待确认点"]
    }
  ],
  "open_questions": ["会后仍需确认的问题"],
  "consolidated_next_steps": ["按优先级排序的最终行动项汇总（5-15 条）"],
  "one_sentence_summary": "一句话概括整个会议"
}"""

_SUMMARY_PROMPT_MEETING = f"""请根据以下会议转录记录，产出会议纪要。

{_SUMMARY_INPUT_HINT}

{_SUMMARY_SCHEMA}

仅输出 JSON，不要任何额外解释。"""

_SUMMARY_PROMPT_DEFAULT = f"""请根据以下转录记录，产出摘要。

{_SUMMARY_INPUT_HINT}

{_SUMMARY_SCHEMA}

仅输出 JSON。"""

_SUMMARY_PROMPT_STRICT = f"""请根据以下转录记录，**仅依据明确出现的内容**产出摘要，不推测、不脑补。

{_SUMMARY_INPUT_HINT}

{_SUMMARY_SCHEMA}

仅输出 JSON。"""

app = FastAPI(title="FunASR OpenAI-Compatible API", version="1.0.0")
SERVER_START_EPOCH = int(time.time())

MODEL_REGISTRY = {}
MODEL_LOAD_STATUS: dict[str, dict] = {}
MODEL_LOAD_LOCK = threading.Lock()
MODEL_LOAD_EVENTS: dict[str, threading.Event] = {}
MODEL_LOAD_ERRORS: dict[str, str] = {}
# ---- 模型闲置释放（5h）：registry_key -> 最后使用时间戳，超时由后台线程卸载 ----
_MODEL_LAST_USED: dict[str, float] = {}
_MODEL_IDLE_TTL_S = int(os.environ.get("FUNASR_MODEL_IDLE_TTL_S", "1800"))  # 0 = 禁用
_MODEL_IDLE_SWEEP_S = max(10, int(os.environ.get("FUNASR_MODEL_IDLE_SWEEP_S", "60")))
DEVICE = "cpu"

from model_catalog import (
    DIARIZATION_MODELS,
    EMOTION_MODELS,
    OFFLINE_ASR_MODELS,
    STREAMING_MODELS,
    get_model_capabilities,
    get_model_configs,
    resolve_local_model_path,
)


MODEL_CONFIGS = get_model_configs()
MODEL_CAPABILITIES = get_model_capabilities()

LANGUAGE_CODE_MAP = {
    "zh": "Chinese", "cn": "Chinese", "chinese": "Chinese",
    "en": "English", "english": "English",
    "ja": "Japanese", "jp": "Japanese", "japanese": "Japanese",
    "ko": "Korean", "kr": "Korean", "korean": "Korean",
    "yue": "Cantonese", "cantonese": "Cantonese",
    "ar": "Arabic", "arabic": "Arabic",
    "de": "German", "german": "German",
    "fr": "French", "french": "French",
    "es": "Spanish", "spanish": "Spanish",
    "pt": "Portuguese", "portuguese": "Portuguese",
    "id": "Indonesian", "indonesian": "Indonesian",
    "it": "Italian", "italian": "Italian",
    "ru": "Russian", "russian": "Russian",
    "th": "Thai", "thai": "Thai",
    "vi": "Vietnamese", "vietnamese": "Vietnamese",
    "tr": "Turkish", "turkish": "Turkish",
    "hi": "Hindi", "hindi": "Hindi",
    "ms": "Malay", "malay": "Malay",
    "nl": "Dutch", "dutch": "Dutch",
    "sv": "Swedish", "swedish": "Swedish",
    "da": "Danish", "danish": "Danish",
    "fi": "Finnish", "finnish": "Finnish",
    "pl": "Polish", "polish": "Polish",
    "cs": "Czech", "czech": "Czech",
    "fil": "Filipino", "filipino": "Filipino",
    "fa": "Persian", "persian": "Persian",
    "el": "Greek", "greek": "Greek",
    "ro": "Romanian", "romanian": "Romanian",
    "hu": "Hungarian", "hungarian": "Hungarian",
    "mk": "Macedonian", "macedonian": "Macedonian",
}


def normalize_language(lang: str | None) -> str | None:
    """将语言码/缩写/全称统一为模型接受的全称格式。"""
    if not lang:
        return None
    normalized = lang.strip().lower()
    if not normalized:
        return None
    return LANGUAGE_CODE_MAP.get(normalized, lang)


STREAMING_SESSIONS: dict[str, dict] = {}
STREAMING_SESSION_TTL_S = int(os.environ.get("FUNASR_STREAMING_SESSION_TTL_S", "3600"))
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
VALID_PUNC_MODES = {"auto", "disabled"}
VALID_MODEL_HUBS = {"ms", "modelscope", "hf", "huggingface"}
MAX_UPLOAD_BYTES = int(os.environ.get("FUNASR_MAX_UPLOAD_BYTES", str(2 * 1024 * 1024 * 1024)))
MAX_STREAM_CHUNK_BYTES = int(
    os.environ.get("FUNASR_MAX_STREAM_CHUNK_BYTES", str(8 * 1024 * 1024))
)
WORKFLOW_TEMP_ROOT = Path(tempfile.gettempdir()) / "pat-funasr-workflows"
WORKFLOW_TEMP_TTL_S = int(os.environ.get("FUNASR_WORKFLOW_TEMP_TTL_S", str(24 * 3600)))
# 任务快照持久化：重启后恢复历史任务（workspace 已被 .gitignore 忽略）
WORKFLOW_DB_PATH = os.environ.get(
    "FUNASR_WORKFLOW_DB_PATH",
    str(_PROJECT_ROOT / "workspace" / "workflow_jobs.db"),
)
WORKFLOW_MANAGER = workflow_service.WorkflowJobManager(
    max_workers=int(os.environ.get("FUNASR_WORKFLOW_MAX_WORKERS", "1")),
    terminal_callback=artifact_service.refresh_events_artifact,
    terminal_ttl_s=int(os.environ.get("FUNASR_WORKFLOW_JOB_TTL_S", str(24 * 3600))),
    max_terminal_jobs=int(os.environ.get("FUNASR_WORKFLOW_MAX_TERMINAL_JOBS", "100")),
    db_path=WORKFLOW_DB_PATH,
)


def _workflow_runner_not_configured(context: workflow_service.WorkflowRunContext) -> dict:
    """在完整执行器接入前返回明确错误，避免任务假完成。"""
    context.emit(
        level="error",
        stage="workflow",
        progress=0.0,
        message="工作流执行器尚未配置",
        error_code="WORKFLOW_RUNNER_NOT_CONFIGURED",
        retryable=False,
    )
    raise RuntimeError("工作流执行器尚未配置")


WORKFLOW_RUNNER = _workflow_runner_not_configured


def _cleanup_workflow_temp_dirs(now: float | None = None) -> int:
    """删除超过 TTL 的非活动任务目录，返回清理数量。"""
    if not WORKFLOW_TEMP_ROOT.exists():
        return 0
    current_time = float(now if now is not None else time.time())
    active_roots = {
        Path(path).resolve().parent
        for path in WORKFLOW_MANAGER.active_source_paths()
    }
    removed = 0
    root = WORKFLOW_TEMP_ROOT.resolve()
    for child in WORKFLOW_TEMP_ROOT.iterdir():
        try:
            resolved = child.resolve()
            if resolved.parent != root or not child.is_dir() or resolved in active_roots:
                continue
            if current_time - child.stat().st_mtime <= WORKFLOW_TEMP_TTL_S:
                continue
            shutil.rmtree(resolved)
            removed += 1
        except FileNotFoundError:
            continue
        except Exception as exc:
            logger.warning("工作流临时目录清理失败 %s: %s", child, exc)
    return removed


def get_default_model_hub() -> str | None:
    """读取项目默认模型来源；空值表示使用模型静态配置。"""
    raw = os.environ.get("FUNASR_MODEL_HUB", "").strip().lower()
    if not raw:
        return None
    if raw not in VALID_MODEL_HUBS:
        logger.warning("Ignoring unsupported FUNASR_MODEL_HUB=%s", raw)
        return None
    return "ms" if raw == "modelscope" else "hf" if raw == "huggingface" else raw


def _parse_chunk_size(raw: str) -> list[int]:
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("chunk_size 不能为空，应为形如 '0,10,5' 的 3 个整数")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError("chunk_size 必须是 3 个整数，例如 '0,10,5'")
    try:
        values = [int(p) for p in parts]
    except ValueError as exc:
        raise ValueError("chunk_size 必须是整数，例如 '0,10,5'") from exc
    if any(v < 0 for v in values):
        raise ValueError("chunk_size 不允许为负数")
    return values


def _cleanup_streaming_sessions(now: float) -> None:
    for session_id, state in list(STREAMING_SESSIONS.items()):
        updated_at = state.get("updated_at", 0.0)
        if now - float(updated_at) > STREAMING_SESSION_TTL_S:
            STREAMING_SESSIONS.pop(session_id, None)


def _get_or_create_streaming_session(
    *,
    session_id: Optional[str],
    model: str,
    reset: bool,
    now: float,
) -> tuple[str, dict]:
    if not session_id:
        session_id = uuid.uuid4().hex
    if reset or session_id not in STREAMING_SESSIONS:
        STREAMING_SESSIONS[session_id] = {
            "model": model,
            "cache": {},
            "full_text": "",
            "updated_at": now,
            # per-session 锁：串行化同会话的 generate + cache 读写，避免并发请求污染
            "lock": threading.Lock(),
        }
        return session_id, STREAMING_SESSIONS[session_id]
    state = STREAMING_SESSIONS[session_id]
    if state.get("model") != model:
        raise ValueError(f"session_id '{session_id}' 已绑定模型 '{state.get('model')}'，不能切换为 '{model}'")
    state["updated_at"] = now
    return session_id, state



def build_model_runtime_config(
    *,
    model_name: str,
    device: Optional[str],
    hub: Optional[str],
    disable_update: Optional[bool],
    ncpu: Optional[int],
    log_level: Optional[str],
    disable_pbar: Optional[bool],
    punc_mode: Optional[str],
    spk_model: Optional[str] = None,
    forced_aligner: Optional[str] = None,
) -> dict:
    """基于静态模型配置与运行时覆写项，生成最终 AutoModel 配置。"""
    if model_name not in MODEL_CONFIGS:
        available = list(MODEL_CONFIGS.keys())
        raise ValueError(f"Unknown model '{model_name}'. Available: {available}")

    cfg = MODEL_CONFIGS[model_name].copy()
    cfg["device"] = device or DEVICE
    cfg["disable_update"] = True if disable_update is None else bool(disable_update)

    effective_hub = hub or get_default_model_hub()
    if effective_hub:
        cfg["hub"] = effective_hub
    if ncpu is not None:
        if int(ncpu) <= 0:
            raise ValueError("ncpu must be > 0")
        cfg["ncpu"] = int(ncpu)
    if log_level:
        normalized_log_level = str(log_level).upper()
        if normalized_log_level not in VALID_LOG_LEVELS:
            raise ValueError(f"log_level must be one of {sorted(VALID_LOG_LEVELS)}")
        cfg["log_level"] = normalized_log_level
    if disable_pbar is not None:
        cfg["disable_pbar"] = bool(disable_pbar)

    normalized_punc_mode = str(punc_mode or "auto").lower()
    if normalized_punc_mode not in VALID_PUNC_MODES:
        raise ValueError(f"punc_mode must be one of {sorted(VALID_PUNC_MODES)}")
    if normalized_punc_mode == "disabled":
        cfg.pop("punc_model", None)
    if spk_model:
        cfg["spk_model"] = spk_model
    if forced_aligner:
        cfg["forced_aligner"] = forced_aligner
    return cfg


def build_model_registry_key(model_name: str, cfg: dict) -> str:
    """根据运行时配置生成稳定 registry key，避免不同变体互相污染。"""
    variant_parts = [
        f"device={cfg.get('device', DEVICE)}",
        f"hub={cfg.get('hub', '')}",
        f"disable_update={bool(cfg.get('disable_update', True))}",
        f"ncpu={cfg.get('ncpu', '')}",
        f"log_level={cfg.get('log_level', '')}",
        f"disable_pbar={cfg.get('disable_pbar', '')}",
        f"punc_model={cfg.get('punc_model', '')}",
        f"spk_model={cfg.get('spk_model', '')}",
        f"forced_aligner={cfg.get('forced_aligner', '')}",
    ]
    return f"{model_name}::{'|'.join(variant_parts)}"


def _is_model_ready(model_name: str) -> bool:
    """只按模型主名判断是否已有至少一个变体加载完成。"""
    return any(key == model_name or key.startswith(f"{model_name}::") for key in MODEL_REGISTRY)


def _loaded_model_names() -> list[str]:
    """返回已加载的模型主名列表，避免把内部变体 key 暴露到用户界面。"""
    return sorted({str(key).split("::", 1)[0] for key in MODEL_REGISTRY})


def _sweep_idle_models() -> int:
    """卸载超过空闲阈值的模型并释放显存，返回卸载数量。

    只在 FUNASR_MODEL_IDLE_TTL_S > 0 时生效；错误单个跳过不影响其余模型。
    """
    if _MODEL_IDLE_TTL_S <= 0:
        return 0
    now = time.time()
    with MODEL_LOAD_LOCK:
        expired_keys = [
            key
            for key, last_used in list(_MODEL_LAST_USED.items())
            if now - last_used > _MODEL_IDLE_TTL_S
        ]
    unloaded = 0
    for key in expired_keys:
        try:
            with MODEL_LOAD_LOCK:
                model = MODEL_REGISTRY.pop(key, None)
                _MODEL_LAST_USED.pop(key, None)
                if model is None:
                    continue
                # 主名可能是 "model" 或 "model::variant"，统一清理加载状态
                base_name = str(key).split("::", 1)[0]
                MODEL_LOAD_STATUS.pop(base_name, None)
        except Exception as exc:
            logger.warning("模型 %s 闲置卸载失败: %s", key, exc)
            continue
        # 引用释放后回收，显存仅对 CUDA 生效
        del model
        unloaded += 1
        logger.info("模型 %s 闲置超过 %ss 已卸载", key, _MODEL_IDLE_TTL_S)
    if unloaded:
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
    return unloaded


def _model_idle_reaper_loop() -> None:
    """后台守护循环：周期扫描并卸载闲置模型，释放显存。"""
    while True:
        time.sleep(_MODEL_IDLE_SWEEP_S)
        try:
            _sweep_idle_models()
        except Exception:
            logger.exception("模型闲置释放扫描失败")


def _start_model_idle_reaper() -> None:
    """启动闲置释放后台线程（daemon，进程退出自动终止）。"""
    threading.Thread(
        target=_model_idle_reaper_loop,
        name="pat-model-idle-reaper",
        daemon=True,
    ).start()


class ModelNotDownloadedError(RuntimeError):
    """模型或其运行依赖未在本地缓存中找到。"""


def _model_cache_roots() -> list[Path]:
    roots = [
        # 本机共享缓存优先：ModelScope 全局缓存
        Path.home() / ".cache" / "modelscope" / "hub" / "models",
        # HuggingFace Hub 默认缓存目录（支持 models--Org--Name/snapshots/<hash>/ 布局）
        Path.home() / ".cache" / "huggingface" / "hub",
        # 备选：项目工作区模型目录
        _PROJECT_ROOT / "workspace" / "models",
    ]
    # 环境变量可覆写 HF 缓存路径
    configured_hf = os.environ.get("HUGGINGFACE_HUB_CACHE", "").strip()
    if configured_hf:
        roots.insert(0, Path(configured_hf))
    configured_cache = os.environ.get("MODELSCOPE_CACHE", "").strip()
    if configured_cache:
        roots.insert(0, Path(configured_cache))
    return roots


def _resolve_runtime_models_to_local(cfg: dict) -> dict:
    """把模型及依赖解析为本地路径；缺失时不强制 raise（让 FunASR 内部自己解析）。

    双重防联网策略：
    1. cfg["check_latest"] = False → download_from_ms 里 check_latest=False 跳过远端校验
    2. 能找到本地路径时加 model_path / vad_model_path 等 → download_from_ms 看到 "model_path" in kwargs 就跳过 snapshot_download
    找不到时只打 warning（FunASR 内部有完整 alias 表 + 缓存查找逻辑）。
    """
    missing: list[str] = []
    # 主模型：保留注册 key，尝试加 model_path
    configured = str(cfg.get("model") or "").strip()
    if configured:
        if os.path.exists(configured):
            cfg["model_path"] = str(Path(configured).resolve())
        else:
            local_path = resolve_local_model_path(configured, _model_cache_roots())
            if local_path is not None:
                cfg["model_path"] = str(local_path)
            else:
                logger.info("主模型 %s 未在 resolve_local_model_path 命中，交由 FunASR 内部解析", configured)
    # 辅助模型：原 key 保留，尝试加 xxx_path
    for key in ("vad_model", "punc_model", "spk_model", "forced_aligner"):
        val = str(cfg.get(key) or "").strip()
        if not val:
            continue
        if os.path.exists(val):
            cfg[f"{key}_path"] = str(Path(val).resolve())
        else:
            local_path = resolve_local_model_path(val, _model_cache_roots())
            if local_path is not None:
                cfg[f"{key}_path"] = str(local_path)
                logger.info("Using local %s: %s", key, local_path)
            else:
                missing.append(f"{key}={val}")
                logger.warning("%s 未在本地缓存命中，FunASR 将内部解析（check_latest=False 可阻止联网）", key)
    # 不再 raise —— FunASR 内部有完整 alias 表 + get_or_download_model_dir 查缓存
    # 配合 check_latest=False + 已下载的模型不会真的联网
    cfg["check_latest"] = False
    return cfg


def _model_load_state(model_name: str) -> dict:
    """返回模型加载状态；ready 以缓存为最终依据。"""
    if model_name not in MODEL_CONFIGS:
        raise ValueError(f"Unknown model '{model_name}'. Available: {list(MODEL_CONFIGS.keys())}")
    status = MODEL_LOAD_STATUS.get(model_name, {})
    if _is_model_ready(model_name):
        state = "ready"
    else:
        state = str(status.get("state") or "not_loaded")
    return {
        "model": model_name,
        "ready": state == "ready",
        "state": state,
        "error": status.get("error"),
        "updated_at": status.get("updated_at"),
    }


def _unload_translation_models_except(keep_model: str) -> None:
    """卸载除 keep_model 外的所有已驻留翻译模型，释放 GPU 显存。

    翻译为串行任务，同一时刻仅保留一个翻译模型，避免 NLLB 1.3B/600M (fp16)
    与 TranslateGemma GGUF 同时驻留挤占显存。FunASR 不在此卸载，避免打断
    并发进行的转录任务。
    """
    import torch
    for key in list(MODEL_REGISTRY):
        model_name = key.split("::", 1)[0]
        if model_name == keep_model:
            continue
        model = MODEL_REGISTRY.get(key)
        if model is None or not hasattr(model, "translate"):
            continue
        # llama.cpp 实例显式释放底层 context（GGUF 分支）
        llm = getattr(model, "llm", None)
        close = getattr(llm, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.warning("关闭 GGUF 实例失败（无害）", exc_info=True)
        # 释放 transformers 模型权重（NLLB / TranslateGemma transformers 分支）
        model_obj = getattr(model, "model", None)
        if model_obj is not None:
            try:
                del model_obj
            except Exception:
                pass
        MODEL_REGISTRY.pop(key, None)
        _MODEL_LAST_USED.pop(key, None)
        if model_name in MODEL_LOAD_STATUS:
            MODEL_LOAD_STATUS[model_name] = {"state": "unloaded", "error": None, "updated_at": time.time()}
        logger.info("翻译模型 %s 已卸载（切换到 %s）", model_name, keep_model)
        del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _unload_funasr_models() -> None:
    """卸载所有已驻留的非翻译模型（FunASR），腾出显存。

    仅在翻译模型（尤其 GGUF/llama.cpp）加载前显存余量不足时调用：
    llama.cpp 在显存不足时会在 C++ 层 abort() 直接杀死进程（网关 502，Python
    无法捕获）。ASR 模型下次转录时由 load_model 自动重新加载。
    """
    import torch
    for key in list(MODEL_REGISTRY):
        model = MODEL_REGISTRY.get(key)
        if model is None or hasattr(model, "translate"):
            continue
        model_name = key.split("::", 1)[0]
        model_obj = getattr(model, "model", None)
        if model_obj is not None:
            try:
                del model_obj
            except Exception:
                pass
        MODEL_REGISTRY.pop(key, None)
        _MODEL_LAST_USED.pop(key, None)
        if model_name in MODEL_LOAD_STATUS:
            MODEL_LOAD_STATUS[model_name] = {"state": "unloaded", "error": None, "updated_at": time.time()}
        logger.info("FunASR 模型 %s 已卸载（翻译模型需要显存）", model_name)
        del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def load_model(
    model_name: str,
    *,
    device: Optional[str] = None,
    hub: Optional[str] = None,
    disable_update: Optional[bool] = None,
    ncpu: Optional[int] = None,
    log_level: Optional[str] = None,
    disable_pbar: Optional[bool] = None,
    punc_mode: Optional[str] = None,
    spk_model: Optional[str] = None,
    forced_aligner: Optional[str] = None,
):
    """Load a model and store in registry. Thread-safe via MODEL_LOAD_LOCK."""
    cfg = build_model_runtime_config(
        model_name=model_name,
        device=device,
        hub=hub,
        disable_update=disable_update,
        ncpu=ncpu,
        log_level=log_level,
        disable_pbar=disable_pbar,
        punc_mode=punc_mode,
        spk_model=spk_model,
        forced_aligner=forced_aligner,
    )
    # 在登记 single-flight 事件前完成本地解析，避免缺失模型留下永不唤醒的等待者。
    cfg = _resolve_runtime_models_to_local(cfg)
    registry_key = build_model_registry_key(model_name, cfg)

    is_loader = False
    with MODEL_LOAD_LOCK:
        # B: 翻译为串行任务，切换模型时先卸载其他翻译模型（FunASR 保留，
        #    避免打断并发转录）；GGUF 显存不足时再单独卸载 FunASR
        if cfg.get("type") == "translation":
            _unload_translation_models_except(model_name)
        if registry_key in MODEL_REGISTRY:
            # 命中缓存：刷新最后使用时间（每次 ASR 请求都会走到这里）
            _MODEL_LAST_USED[registry_key] = time.time()
            MODEL_LOAD_STATUS[model_name] = {"state": "ready", "error": None, "updated_at": time.time()}
            return MODEL_REGISTRY[registry_key]
        load_event = MODEL_LOAD_EVENTS.get(registry_key)
        if load_event is None:
            load_event = threading.Event()
            MODEL_LOAD_EVENTS[registry_key] = load_event
            MODEL_LOAD_ERRORS.pop(registry_key, None)
            is_loader = True

    if not is_loader:
        wait_timeout_s = float(os.environ.get("FUNASR_MODEL_LOAD_WAIT_TIMEOUT_S", "1800"))
        if not load_event.wait(timeout=wait_timeout_s):
            raise TimeoutError(
                f"Timed out waiting for model '{model_name}' to finish loading"
            )
        with MODEL_LOAD_LOCK:
            if registry_key in MODEL_REGISTRY:
                return MODEL_REGISTRY[registry_key]
            error = MODEL_LOAD_ERRORS.get(registry_key) or "unknown model load error"
        raise RuntimeError(f"Model '{model_name}' load failed: {error}")

    # 服务请求只允许加载本地模型；下载必须通过独立、显式操作完成。
    model_id = cfg["model"]

    logger.info(f"Loading model '{model_name}' on {cfg['device']}...")
    MODEL_LOAD_STATUS[model_name] = {"state": "loading", "error": None, "updated_at": time.time()}
    t0 = time.time()
    try:
        if cfg.get("type") == "translation":
            device_val = cfg.get("device", DEVICE)

            # 优先使用 resolve_local_model_path 写入的本地缓存路径，
            # 避免 transformers 拿着原始模型 ID 尝试联网下载（无网时 500）
            model_dir = str(cfg.get("model_path") or model_id)
            # 本地缓存存在则禁止联网；否则允许联网下载（如 HF gated 模型）
            local_only = bool(cfg.get("model_path"))

            # 屏蔽 "Torch was not compiled with flash attention" 警告（自动回退到标准 SDPA，不影响功能）
            import warnings
            warnings.filterwarnings("ignore", message=".*Torch was not compiled with flash attention.*", category=UserWarning)

            gemma_model = None
            if cfg.get("format") == "gguf":
                # ---- TranslateGemma GGUF 量化版（llama.cpp 推理，免 HF gated token）----
                # 在 resolve 出的本地缓存目录中定位 .gguf 权重文件（排除多模态 mmproj）
                gguf_path = None
                if model_dir and os.path.isdir(model_dir):
                    candidates = [
                        os.path.join(model_dir, f)
                        for f in os.listdir(model_dir)
                        if f.endswith(".gguf") and ".mmproj" not in f
                    ]
                    if candidates:
                        gguf_path = candidates[0]
                if not gguf_path:
                    # 本地缓存未命中：自动下载到本机共享缓存（HF 全局缓存），下载后重新定位 .gguf
                    try:
                        from huggingface_hub import snapshot_download
                        snapshot_dir = snapshot_download(repo_id=cfg.get("model", model_id))
                    except Exception as exc:
                        raise FileNotFoundError(
                            f"GGUF 模型下载失败 {cfg.get('model', model_id)}: {exc}"
                        ) from exc
                    if snapshot_dir and os.path.isdir(snapshot_dir):
                        candidates = [
                            os.path.join(snapshot_dir, f)
                            for f in os.listdir(snapshot_dir)
                            if f.endswith(".gguf") and ".mmproj" not in f
                        ]
                        if candidates:
                            gguf_path = candidates[0]
                if not gguf_path:
                    raise FileNotFoundError(
                        f"GGUF 模型权重未找到（请先下载 {cfg.get('model', model_id)} 到本地缓存）: {model_dir}"
                    )
                from llama_cpp import Llama as LlamaCpp
                logger.info(f"Loading TranslateGemma GGUF from {gguf_path}...")
                # 优先 GPU 全量 offload；显存余量不足或 GPU 加载失败时回退 CPU。
                # 关键：llama.cpp 在显存不足时会在 C++ 层 abort() 直接杀死进程（Python 无法捕获，
                # 表现为网关 502 Bad Gateway），因此必须在加载前主动检查显存余量。
                n_gpu_layers = -1
                try:
                    import torch
                    if torch.cuda.is_available():
                        free_bytes, _ = torch.cuda.mem_get_info()
                        if free_bytes < 4 * 1024 ** 3:
                            # 显存不足：先卸载 FunASR 腾出显存再重试 GPU；仍不足才回退 CPU。
                            # 关键：llama.cpp 显存不足时会在 C++ 层 abort() 直接杀死进程
                            # （Python 无法捕获，表现为网关 502），必须提前腾出足够显存。
                            logger.warning(
                                "GPU 显存余量不足（%.1fGB < 4GB），先卸载 FunASR 模型腾出显存",
                                free_bytes / 1024 ** 3,
                            )
                            _unload_funasr_models()
                            free_bytes, _ = torch.cuda.mem_get_info()
                            if free_bytes < 4 * 1024 ** 3:
                                logger.warning(
                                    "卸载 FunASR 后显存仍不足（%.1fGB），GGUF 回退 CPU 推理",
                                    free_bytes / 1024 ** 3,
                                )
                                n_gpu_layers = 0
                except Exception:
                    pass
                try:
                    llm_obj = LlamaCpp(model_path=gguf_path, n_ctx=2048, n_gpu_layers=n_gpu_layers, verbose=False)
                except Exception as exc:
                    logger.warning("GGUF GPU 加载失败（%s），回退 CPU", exc)
                    llm_obj = LlamaCpp(model_path=gguf_path, n_ctx=2048, n_gpu_layers=0, verbose=False)

                class TranslateGemmaGgufTranslationModel:
                    """TranslateGemma Q4_K_M GGUF 量化版翻译模型：llama.cpp 推理。

                    免 HF gated token（社区量化仓库），语言码映射为 ISO 639-1，
                    prompt 手动拼 Gemma3 chat template（<start_of_turn>...）。
                    """

                    # FLORES-200 码 → ISO 639-1 码（与 transformers 版一致）
                    _FLORES_TO_ISO = {
                        "zho_Hans": "zh",
                        "zho_Hant": "zh-Hant",
                        "eng_Latn": "en",
                        "jpn_Jpan": "ja",
                        "kor_Kore": "ko",
                        "fra_Latn": "fr",
                        "tha_Thai": "th",
                        "zsm_Latn": "ms",
                        "vie_Latn": "vi",
                    }

                    def __init__(self, llm):
                        self.llm = llm

                    def _to_iso(self, lang: str) -> str:
                        """FLORES 码映射为 ISO 639-1；不支持时抛错。"""
                        iso = self._FLORES_TO_ISO.get(lang)
                        if not iso:
                            raise ValueError(
                                f"TranslateGemma 不支持语言码 {lang!r}，支持: {sorted(self._FLORES_TO_ISO)}"
                            )
                        return iso

                    def _translate_one(self, t: str, source_lang: str, target_lang: str, max_tokens: int) -> str:
                        """翻译单段文本（≤1200 字）：Gemma3 prompt + greedy 解码。"""
                        src_iso = self._to_iso(source_lang)
                        tgt_iso = self._to_iso(target_lang)
                        prompt = (
                            f"<start_of_turn>user\nTranslate the following text from {src_iso} to {tgt_iso}:\n"
                            f"{t}<end_of_turn>\n<start_of_turn>model\n"
                        )
                        out = self.llm(prompt, max_tokens=max_tokens, temperature=0.0, stop=["<end_of_turn>"])
                        choices = (out or {}).get("choices") or []
                        return (choices[0].get("text") or "").strip() if choices else ""

                    def translate(self, text, source_lang: str, target_lang: str, num_beams: int = 1, max_length: int = 512):
                        """翻译文本：自动分块（≤1200 字/块）；num_beams 对 decoder-only 无效，max_length 作 max_tokens 下限。"""
                        import re as _re
                        max_tokens = max(int(max_length), 1024)
                        is_list = isinstance(text, list)
                        texts = text if is_list else [text]
                        outputs = []
                        for t in texts:
                            if not t:
                                outputs.append("")
                                continue
                            # 短文本直接走
                            if len(t) <= 1200:
                                outputs.append(self._translate_one(t, source_lang, target_lang, max_tokens))
                                continue
                            # 长文本：按换行/句号切分，累计 ≤1200 字成一块
                            raw_parts = t.split("\n")
                            chunks: list[str] = []
                            cur = ""
                            for part in raw_parts:
                                sentences = _re.split(r"(?<=[。！？!?\.])\s*", part.strip())
                                for s in sentences:
                                    if not s:
                                        continue
                                    if len(cur) + len(s) > 1200:
                                        if cur:
                                            chunks.append(cur)
                                        if len(s) > 1200:
                                            chunks.append(s)
                                            cur = ""
                                        else:
                                            cur = s
                                    else:
                                        cur += s
                                cur += "\n"
                            if cur.strip():
                                chunks.append(cur.strip())
                            # 逐块翻译
                            translated_parts = []
                            for chunk in chunks:
                                translated_parts.append(self._translate_one(chunk, source_lang, target_lang, max_tokens))
                            outputs.append("\n".join(translated_parts))
                        return outputs if is_list else outputs[0]

                gemma_model = TranslateGemmaGgufTranslationModel(llm_obj)
            elif "translategemma" in model_id:
                # ---- TranslateGemma（Gemma3 decoder-only，chat template 驱动，与 NLLB 编码方式不同）----
                from transformers import AutoModelForImageTextToText, AutoProcessor
                logger.info(f"Loading TranslateGemma processor & model from {model_dir}...")
                processor = AutoProcessor.from_pretrained(model_dir, local_files_only=local_only)
                model_obj = AutoModelForImageTextToText.from_pretrained(model_dir, local_files_only=local_only)
                # 优先 GPU：CUDA 不可用或显存不足时回退 CPU
                if device_val != "cpu":
                    try:
                        model_obj = model_obj.to(device_val)
                    except Exception as exc:
                        logger.warning("模型 %s 无法加载到 %s（%s），回退 CPU", model_name, device_val, exc)
                        device_val = "cpu"
                        model_obj = model_obj.to("cpu")
                else:
                    model_obj = model_obj.to("cpu")

                class TranslateGemmaTranslationModel:
                    """Gemma3 decoder-only 翻译模型：chat template 构造 prompt，语言码映射为 ISO 639-1。

                    与 NLLB 的 encoder-decoder + src_lang/tgt_lang 编码方式不同。
                    """

                    # FLORES-200 码 → TranslateGemma 的 ISO 639-1 码
                    _FLORES_TO_ISO = {
                        "zho_Hans": "zh",
                        "zho_Hant": "zh-Hant",
                        "eng_Latn": "en",
                        "jpn_Jpan": "ja",
                        "kor_Kore": "ko",
                        "fra_Latn": "fr",
                        "tha_Thai": "th",
                        "zsm_Latn": "ms",
                        "vie_Latn": "vi",
                    }

                    def __init__(self, processor, model, device):
                        self.processor = processor
                        self.model = model
                        self.device = device

                    def _to_iso(self, lang: str) -> str:
                        """FLORES 码映射为 ISO 639-1；不支持时抛错（模型卡规定）。"""
                        iso = self._FLORES_TO_ISO.get(lang)
                        if not iso:
                            raise ValueError(
                                f"TranslateGemma 不支持语言码 {lang!r}，支持: {sorted(self._FLORES_TO_ISO)}"
                            )
                        return iso

                    def _translate_one(self, t: str, source_lang: str, target_lang: str, max_new_tokens: int) -> str:
                        """翻译单段文本（≤1200 字）：chat template + generate + decode。"""
                        import torch
                        src_iso = self._to_iso(source_lang)
                        tgt_iso = self._to_iso(target_lang)
                        messages = [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "source_lang_code": src_iso,
                                        "target_lang_code": tgt_iso,
                                        "text": t,
                                    }
                                ],
                            }
                        ]
                        inputs = self.processor.apply_chat_template(
                            messages,
                            tokenize=True,
                            add_generation_prompt=True,
                            return_dict=True,
                            return_tensors="pt",
                        )
                        if self.device != "cpu":
                            inputs = {k: v.to(self.device) for k, v in inputs.items()}
                        input_len = int(inputs["input_ids"].shape[-1])
                        with torch.inference_mode():
                            gen_out = self.model.generate(**inputs, do_sample=False, max_new_tokens=max_new_tokens)
                        decoded = self.processor.decode(gen_out[0][input_len:], skip_special_tokens=True)
                        return decoded.strip() if decoded else ""

                    def translate(self, text, source_lang: str, target_lang: str, num_beams: int = 1, max_length: int = 512):
                        """翻译文本：自动分块（≤1200 字/块），TranslateGemma 上下文 2K tokens。

                        num_beams 对 decoder-only 模型无效，忽略；max_length 作为 max_new_tokens 下限。
                        """
                        import re as _re
                        max_new_tokens = max(int(max_length), 1024)
                        is_list = isinstance(text, list)
                        texts = text if is_list else [text]
                        outputs = []
                        for t in texts:
                            if not t:
                                outputs.append("")
                                continue
                            # 短文本直接走
                            if len(t) <= 1200:
                                outputs.append(self._translate_one(t, source_lang, target_lang, max_new_tokens))
                                continue
                            # 长文本：按换行/句号切分，累计 ≤1200 字成一块
                            raw_parts = t.split("\n")
                            chunks: list[str] = []
                            cur = ""
                            for part in raw_parts:
                                # 换行内的句子再按。！？切分
                                sentences = _re.split(r"(?<=[。！？!?\.])\s*", part.strip())
                                for s in sentences:
                                    if not s:
                                        continue
                                    if len(cur) + len(s) > 1200:
                                        if cur:
                                            chunks.append(cur)
                                        # 单句超 1200 字直接放一块
                                        if len(s) > 1200:
                                            chunks.append(s)
                                            cur = ""
                                        else:
                                            cur = s
                                    else:
                                        cur += s
                                cur += "\n"
                            if cur.strip():
                                chunks.append(cur.strip())
                            # 逐块翻译
                            translated_parts = []
                            for chunk in chunks:
                                translated_parts.append(self._translate_one(chunk, source_lang, target_lang, max_new_tokens))
                            outputs.append("\n".join(translated_parts))
                        return outputs if is_list else outputs[0]

                gemma_model = TranslateGemmaTranslationModel(processor, model_obj, device_val)

            if gemma_model is None:
                from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
                logger.info(f"Loading tokenizer & Seq2Seq model from {model_dir}...")
                tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
                model_obj = AutoModelForSeq2SeqLM.from_pretrained(model_dir, local_files_only=True)
                if device_val != "cpu":
                    # A: NLLB 用 fp16 加载（显存减半），翻译质量损失可忽略；缓解多翻译模型挤爆显存
                    try:
                        model_obj = model_obj.half().to(device_val)
                    except Exception as exc:
                        # CUDA 上下文异常（如与 llama.cpp 混用后上下文损坏）时回退 CPU，避免翻译请求 500
                        logger.warning("NLLB GPU 加载失败（%s），回退 CPU", exc)
                        device_val = "cpu"

                class NLLBTranslationModel:
                    def __init__(self, tokenizer, model, device):
                        self.tokenizer = tokenizer
                        self.model = model
                        self.device = device

                    def _translate_one(self, t: str, source_lang: str, target_lang: str, num_beams: int, max_length: int) -> str:
                        """翻译单段文本（≤500 字），NLLB max_length=512 硬限制"""
                        # 浅拷贝 tokenizer 再设置 src_lang，避免并发翻译请求修改共享实例属性互相污染
                        tokenizer = copy.copy(self.tokenizer)
                        tokenizer.src_lang = source_lang
                        inputs = tokenizer(t, return_tensors="pt")
                        if self.device != "cpu":
                            inputs = {k: v.to(self.device) for k, v in inputs.items()}
                        target_lang_id = tokenizer.convert_tokens_to_ids(target_lang)
                        gen_out = self.model.generate(
                            **inputs,
                            forced_bos_token_id=target_lang_id,
                            max_length=max_length,
                            num_beams=num_beams,
                        )
                        decoded = tokenizer.batch_decode(gen_out, skip_special_tokens=True)
                        return decoded[0] if decoded else ""

                    def translate(self, text, source_lang: str, target_lang: str, num_beams: int = 5, max_length: int = 512):
                        """翻译文本：自动长文本分块（≤500字/块，按句号/感叹号/问号/换行切分），避免 NLLB max_length=512 截断"""
                        import re as _re
                        is_list = isinstance(text, list)
                        texts = text if is_list else [text]
                        outputs = []
                        for t in texts:
                            if not t:
                                outputs.append("")
                                continue
                            # 短文本直接走
                            if len(t) <= 500:
                                outputs.append(self._translate_one(t, source_lang, target_lang, num_beams, max_length))
                                continue
                            # 长文本：按换行/句号切分，累计 ≤500 字成一块
                            raw_parts = t.split("\n")
                            chunks: list[str] = []
                            cur = ""
                            for part in raw_parts:
                                # 换行内的句子再按。！？切分
                                sentences = _re.split(r"(?<=[。！？!?\.])\s*", part.strip())
                                for s in sentences:
                                    if not s:
                                        continue
                                    if len(cur) + len(s) > 500:
                                        if cur:
                                            chunks.append(cur)
                                        # 单句超 500 字直接放一块（NLLB 会截断但不影响整体）
                                        if len(s) > 500:
                                            chunks.append(s)
                                            cur = ""
                                        else:
                                            cur = s
                                    else:
                                        cur += s
                                cur += "\n"
                            if cur.strip():
                                chunks.append(cur.strip())
                            # 逐块翻译
                            translated_parts = []
                            for chunk in chunks:
                                translated_parts.append(self._translate_one(chunk, source_lang, target_lang, num_beams, max_length))
                            outputs.append("\n".join(translated_parts))
                        return outputs if is_list else outputs[0]

                model = NLLBTranslationModel(tokenizer, model_obj, device_val)
            else:
                model = gemma_model
        else:
            from funasr import AutoModel
            model = AutoModel(**cfg)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        with MODEL_LOAD_LOCK:
            MODEL_LOAD_STATUS[model_name] = {
                "state": "error",
                "error": str(exc),
                "updated_at": time.time(),
            }
            MODEL_LOAD_ERRORS[registry_key] = str(exc)
            event = MODEL_LOAD_EVENTS.pop(registry_key, None)
            if event is not None:
                event.set()
        raise
    elapsed = time.time() - t0
    logger.info(f"Model '{model_name}' loaded in {elapsed:.1f}s")

    with MODEL_LOAD_LOCK:
        MODEL_REGISTRY[registry_key] = model
        _MODEL_LAST_USED[registry_key] = time.time()
        MODEL_LOAD_STATUS[model_name] = {"state": "ready", "error": None, "updated_at": time.time()}
        MODEL_LOAD_ERRORS.pop(registry_key, None)
        event = MODEL_LOAD_EVENTS.pop(registry_key, None)
        if event is not None:
            event.set()
    return model


def clean_text(text: str) -> str:
    """Remove SenseVoice special tags and residual special tokens from output."""
    # Remove all <|...|> special tags produced by SenseVoice tokenizer
    text = re.sub(r'<\|[^|]*\|>', '', text)
    # Remove residual angle-bracket tokens that might slip through
    text = re.sub(r'<[^<>]*>', '', text)
    # Clean up any double spaces or control chars
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def merge_streaming_text(previous_text: str, chunk_text: str) -> str:
    """合并流式文本，兼容分片增量和累计假设两种返回语义。"""
    previous = clean_text(previous_text)
    current = clean_text(chunk_text)
    if not previous:
        return current
    if not current:
        return previous
    if current == previous or previous.endswith(current):
        return previous

    max_overlap = min(len(previous), len(current), 500)
    for overlap in range(max_overlap, 0, -1):
        if previous.endswith(current[:overlap]):
            return previous + current[overlap:]
    return previous + current


def pcm16_bytes_to_float32_audio(chunk: bytes) -> np.ndarray:
    """把 PCM16 little-endian 分片解码为 FunASR streaming 官方示例使用的 float32 采样数组。"""
    if not chunk:
        raise ValueError("上传分片为空")
    usable = len(chunk) - (len(chunk) % 2)
    if usable <= 0:
        raise ValueError("PCM16 分片长度无效")
    samples = np.frombuffer(chunk[:usable], dtype="<i2")
    if samples.size == 0:
        raise ValueError("PCM16 分片没有有效采样点")
    return samples.astype(np.float32) / 32768.0


def build_native_mic_stream_html() -> str:
    """生成绕过 Gradio Audio 的原生浏览器 Mic 实时识别页面。"""
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pat-FunASR Mic 实时识别</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #ffffff;
      --panel: #ffffff;
      --text: #111827;
      --muted: #4b5563;
      --border: #e5e7eb;
      --control-border: #d1d5db;
      --control-bg: #ffffff;
      --subtle: #f9fafb;
      --meter-bg: #f3f4f6;
      --primary: #2563eb;
      --danger: #dc2626;
      --ok: #16a34a;
      --wave-bg: #111827;
      --wave-line: #22c55e;
    }
    :root[data-theme="light"] {
      color-scheme: light;
    }
    :root[data-theme="dark"] {
      --bg: #111827;
      --panel: #1f2937;
      --text: #f9fafb;
      --muted: #d1d5db;
      --border: #374151;
      --control-border: #4b5563;
      --control-bg: #111827;
      --subtle: #111827;
      --meter-bg: #374151;
      --primary: #3b82f6;
      --danger: #ef4444;
      --ok: #22c55e;
      --wave-bg: #030712;
      --wave-line: #4ade80;
    }
    @media (prefers-color-scheme: dark) {
      :root:not([data-theme="light"]) {
        --bg: #111827;
        --panel: #1f2937;
        --text: #f9fafb;
        --muted: #d1d5db;
        --border: #374151;
        --control-border: #4b5563;
        --control-bg: #111827;
        --subtle: #111827;
        --meter-bg: #374151;
        --primary: #3b82f6;
        --danger: #ef4444;
        --ok: #22c55e;
        --wave-bg: #030712;
        --wave-line: #4ade80;
      }
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; overflow: hidden; }
    body { margin: 0; background: transparent; color: var(--text); font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif; font-size: 14px; }
    main { width: 100%; max-width: 1120px; height: 100%; margin: 0 auto; padding: 10px; overflow: hidden; }
    h1 { margin: 0 0 8px; font-size: 18px; font-weight: 600; letter-spacing: 0; }
    .grid { display: grid; grid-template-columns: 300px minmax(0, 1fr); gap: 10px; align-items: stretch; height: calc(100% - 34px); min-height: 0; }
    section { background: var(--panel); border: 0; border-radius: 8px; padding: 10px; min-height: 0; overflow: hidden; }
    label { display: block; color: var(--muted); font-size: 12px; margin: 8px 0 5px; }
    select, input, textarea { width: 100%; border: 1px solid var(--control-border); border-radius: 6px; padding: 7px 9px; font: inherit; background: var(--control-bg); color: var(--text); }
    select, input { min-height: 34px; }
    textarea { resize: none; line-height: 1.45; }
    .row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
    button, a { min-height: 34px; border-radius: 6px; border: 1px solid var(--control-border); background: var(--control-bg); color: var(--text); padding: 6px 11px; font: inherit; text-decoration: none; cursor: pointer; }
    button.primary { background: var(--primary); border-color: var(--primary); color: white; }
    button.stop { background: var(--danger); border-color: var(--danger); color: white; }
    button:disabled, a[aria-disabled="true"] { opacity: .45; cursor: not-allowed; pointer-events: none; }
    .meter { height: 10px; margin: 7px 0; border-radius: 999px; background: var(--meter-bg); border: 1px solid var(--border); overflow: hidden; }
    .meter div { width: 0%; height: 100%; background: var(--ok); transition: width .08s linear; }
    .stats { display: flex; gap: 14px; align-items: center; flex-wrap: nowrap; margin: 7px 0 8px; color: var(--muted); font-size: 12px; white-space: nowrap; }
    .stat strong { color: var(--text); font-size: 13px; font-weight: 600; margin-left: 4px; }
    canvas { width: 100%; height: 72px; border: 1px solid var(--border); border-radius: 8px; background: var(--wave-bg); display: block; }
    .status { color: var(--muted); min-height: 18px; margin: 0 0 5px; font-size: 12px; }
    details { margin-top: 10px; border-top: 1px solid var(--border); padding-top: 8px; }
    summary { cursor: pointer; color: var(--muted); font-size: 13px; }
    .muted { color: var(--muted); font-size: 12px; margin: 7px 0 0; }
    .control-section, .result-section { display: flex; flex-direction: column; }
    .download-row { margin-top: auto; padding-top: 10px; }
    #transcriptBox { flex: 1; min-height: 150px; }
    #logBox { height: 70px; margin-top: 7px; }
    @media (max-width: 880px) { html, body { height: auto; overflow: auto; } main { height: auto; padding: 10px; overflow: visible; } .grid { grid-template-columns: 1fr; height: auto; } section { overflow: visible; } #transcriptBox { height: 180px; } }
  </style>
  <script>
    function applyTheme(theme) {
      if (theme === "light" || theme === "dark") {
        document.documentElement.dataset.theme = theme;
        return;
      }
      document.documentElement.removeAttribute("data-theme");
    }
    const requestedTheme = new URLSearchParams(location.search).get("theme");
    applyTheme(requestedTheme);
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      if (!new URLSearchParams(location.search).get("theme")) applyTheme("auto");
    });
    window.addEventListener("message", (event) => {
      let originHost = "";
      try { originHost = new URL(event.origin).hostname; } catch (_) {}
      if (originHost !== "127.0.0.1" && originHost !== "localhost") return;
      const theme = event.data && event.data.type === "pat-theme" ? event.data.theme : "";
      applyTheme(theme);
    });
  </script>
</head>
<body>
  <main>
    <h1>Mic 实时识别</h1>
    <div class="grid">
      <section class="control-section">
        <label for="deviceSelect">输入设备</label>
        <select id="deviceSelect"><option value="">系统默认输入设备</option></select>
        <div class="row">
          <button id="refreshButton">刷新设备</button>
          <button id="startButton" class="primary" disabled>开始录制并识别</button>
          <button id="stopButton" class="stop" disabled>停止录制</button>
        </div>
        <div class="row download-row">
          <a id="downloadLink" aria-disabled="true">下载录音</a>
        </div>
        <details>
          <summary>高级设置</summary>
          <label for="modelInput">模型</label>
          <input id="modelInput" value="paraformer-zh-streaming">
          <label for="chunkSizeInput">chunk_size</label>
          <input id="chunkSizeInput" value="0,30,15">
          <p class="muted">默认约 1.8 秒一片，减少短词切碎。</p>
        </details>
      </section>
      <section class="result-section">
        <p id="status" class="status">未开始。</p>
        <div class="meter"><div id="levelBar"></div></div>
        <div class="stats">
          <div class="stat">峰值<strong id="peakValue">0.0000</strong></div>
          <div class="stat">RMS<strong id="rmsValue">0.0000</strong></div>
          <div class="stat">分片<strong id="sentValue">0</strong></div>
        </div>
        <canvas id="waveCanvas" width="900" height="180"></canvas>
        <label for="transcriptBox">识别结果</label>
        <textarea id="transcriptBox" readonly></textarea>
        <div class="row download-row">
          <a id="downloadTranscriptLink" aria-disabled="true">下载识别结果</a>
        </div>
        <details>
          <summary>运行日志</summary>
          <textarea id="logBox" readonly style="min-height:120px;"></textarea>
        </details>
      </section>
    </div>
  </main>
  <script>
    const deviceSelect = document.getElementById("deviceSelect");
    const refreshButton = document.getElementById("refreshButton");
    const startButton = document.getElementById("startButton");
    const stopButton = document.getElementById("stopButton");
    const downloadLink = document.getElementById("downloadLink");
    const downloadTranscriptLink = document.getElementById("downloadTranscriptLink");
    const statusEl = document.getElementById("status");
    const levelBar = document.getElementById("levelBar");
    const peakValue = document.getElementById("peakValue");
    const rmsValue = document.getElementById("rmsValue");
    const sentValue = document.getElementById("sentValue");
    const transcriptBox = document.getElementById("transcriptBox");
    const logBox = document.getElementById("logBox");
    const canvas = document.getElementById("waveCanvas");
    const ctx = canvas.getContext("2d");
    let stream = null, audioContext = null, source = null, processor = null, mediaRecorder = null, startupWatchdog = null;
    let flushPromise = Promise.resolve();
    let sessionId = "", sent = 0, running = false, starting = false, pcmBuffer = [], recordedChunks = [], objectUrl = "";
    let transcriptObjectUrl = "";
    let lastPeak = 0, lowSignalFrames = 0, autoRestarted = false;
    let modelReady = false;
    const targetRate = 16000;

    function log(message) {
      const time = new Date().toLocaleTimeString();
      logBox.value += `[${time}] ${message}\\n`;
      logBox.scrollTop = logBox.scrollHeight;
    }
    function setStatus(message) { statusEl.textContent = message; log(message); }
    function formatBrowserError(error) {
      const message = error?.message || String(error);
      if (message.includes("Permission denied") || message.includes("NotAllowedError")) {
        return "麦克风权限被浏览器拒绝，请在地址栏允许此页面使用麦克风后再开始。";
      }
      return message;
    }
    async function microphonePermissionState() {
      try {
        if (!navigator.permissions || !navigator.permissions.query) return "";
        const permission = await navigator.permissions.query({ name: "microphone" });
        return permission.state || "";
      } catch (_) {
        return "";
      }
    }
    function updateTranscriptDownload() {
      if (transcriptObjectUrl) URL.revokeObjectURL(transcriptObjectUrl);
      const text = transcriptBox.value || "";
      if (!text.trim()) {
        downloadTranscriptLink.removeAttribute("href");
        downloadTranscriptLink.setAttribute("aria-disabled", "true");
        return;
      }
      transcriptObjectUrl = URL.createObjectURL(new Blob(["\\ufeff" + text.replace(/\\n/g, "\\r\\n")], { type: "text/plain;charset=utf-8" }));
      downloadTranscriptLink.href = transcriptObjectUrl;
      downloadTranscriptLink.download = `mic-transcript-${Date.now()}.txt`;
      downloadTranscriptLink.setAttribute("aria-disabled", "false");
    }
    function uuid() { return crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2); }
    function selectedModel() {
      return document.getElementById("modelInput").value.trim() || "paraformer-zh-streaming";
    }
    function setModelReady(ready) {
      modelReady = ready;
      startButton.disabled = !ready || running || starting;
    }
    async function fetchModelStatus() {
      const model = encodeURIComponent(selectedModel());
      const response = await fetch(`/v1/models/${model}/status`);
      if (!response.ok) throw new Error(await response.text());
      return await response.json();
    }
    async function preloadSelectedModel() {
      setModelReady(false);
      const modelName = selectedModel();
      setStatus(`正在检查模型 ${modelName}...`);
      const status = await fetchModelStatus();
      if (status.ready) {
        setModelReady(true);
        setStatus(`模型 ${modelName} 已就绪，可以开始录制。`);
        return;
      }
      setStatus(`模型 ${modelName} 加载中，请稍候...`);
      const response = await fetch(`/v1/models/${encodeURIComponent(modelName)}/load`, { method: "POST" });
      if (!response.ok) throw new Error(await response.text());
      const loaded = await response.json();
      if (!loaded.ready) throw new Error(`模型状态异常：${loaded.state || "unknown"}`);
      setModelReady(true);
      setStatus(`模型 ${modelName} 已就绪，可以开始录制。`);
    }
    async function ensureModelReadyBeforeRecording() {
      if (modelReady) return;
      await preloadSelectedModel();
    }
    function currentChunkSamples() {
      const raw = document.getElementById("chunkSizeInput").value.trim() || "0,30,15";
      const parts = raw.split(",").map(part => Number.parseInt(part.trim(), 10));
      if (parts.length !== 3 || parts.some(Number.isNaN) || parts.some(value => value < 0)) {
        return 28800;
      }
      return Math.max(9600, parts[1] * 960);
    }
    function downsample(input, sourceRate) {
      if (sourceRate === targetRate) return input;
      const ratio = sourceRate / targetRate;
      const outLen = Math.floor(input.length / ratio);
      const output = new Float32Array(outLen);
      for (let i = 0; i < outLen; i++) {
        const start = Math.floor(i * ratio);
        const end = Math.min(input.length, Math.floor((i + 1) * ratio));
        let sum = 0, count = 0;
        for (let j = start; j < end; j++) { sum += input[j]; count += 1; }
        output[i] = count ? sum / count : 0;
      }
      return output;
    }
    function floatToPcm16(samples) {
      const bytes = new Uint8Array(samples.length * 2);
      const view = new DataView(bytes.buffer);
      for (let i = 0; i < samples.length; i++) {
        const s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(i * 2, s < 0 ? s * 32768 : s * 32767, true);
      }
      return bytes;
    }
    function draw(samples) {
      const styles = getComputedStyle(document.documentElement);
      ctx.fillStyle = styles.getPropertyValue("--wave-bg").trim() || "#111827";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.strokeStyle = styles.getPropertyValue("--wave-line").trim() || "#22c55e";
      ctx.lineWidth = 2; ctx.beginPath();
      const step = Math.max(1, Math.floor(samples.length / canvas.width));
      for (let x = 0; x < canvas.width; x++) {
        const y = (1 - (samples[x * step] || 0)) * canvas.height / 2;
        if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
    function updateMeter(samples) {
      let peak = 0, sum = 0;
      for (const v of samples) { const a = Math.abs(v); peak = Math.max(peak, a); sum += v * v; }
      const rms = Math.sqrt(sum / Math.max(1, samples.length));
      lastPeak = peak;
      lowSignalFrames = peak < 0.002 && rms < 0.0008 ? lowSignalFrames + 1 : 0;
      peakValue.textContent = peak.toFixed(4);
      rmsValue.textContent = rms.toFixed(4);
      levelBar.style.width = `${Math.min(100, Math.round(peak * 100))}%`;
      draw(samples);
    }
    async function enumerateAudioInputs() {
      if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
        setStatus("当前浏览器不支持 mediaDevices.enumerateDevices。");
        return [];
      }
      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        return devices.filter(d => d.kind === "audioinput");
      } catch (error) {
        log(`设备枚举失败：${error.message}`);
        return [];
      }
    }
    function resetDeviceOptions() {
      deviceSelect.innerHTML = "";
      const def = document.createElement("option");
      def.value = ""; def.textContent = "系统默认输入设备"; deviceSelect.appendChild(def);
    }
    async function refreshDevices(preferConcreteDevice = true) {
      const previousValue = deviceSelect.value;
      resetDeviceOptions();
      let inputs = await enumerateAudioInputs();
      inputs.forEach((d, i) => {
        const option = document.createElement("option");
        option.value = d.deviceId; option.textContent = d.label || `麦克风 ${i + 1}`;
        deviceSelect.appendChild(option);
      });
      if ([...deviceSelect.options].some(option => option.value === previousValue)) {
        deviceSelect.value = previousValue;
      } else if (preferConcreteDevice && inputs.length > 0) {
        const defaultInput = inputs.find(d => d.deviceId === "default");
        deviceSelect.value = (defaultInput || inputs[0]).deviceId;
      }
      const selectedLabel = deviceSelect.selectedOptions[0]?.textContent || "系统默认输入设备";
      const permissionState = await microphonePermissionState();
      if (permissionState === "denied") {
        setStatus(`麦克风权限被拒绝，无法读取真实设备名；当前选择：${selectedLabel}`);
      } else {
        setStatus(`发现 ${inputs.length} 个输入设备；当前选择：${selectedLabel}`);
      }
    }
    async function postChunk(samples, isFinal) {
      const form = new FormData();
      form.append("file", new Blob([floatToPcm16(samples)], { type: "application/octet-stream" }), "chunk.pcm");
      form.append("model", selectedModel());
      form.append("session_id", sessionId);
      form.append("reset", sent === 0 ? "true" : "false");
      form.append("is_final", isFinal ? "true" : "false");
      form.append("chunk_size", document.getElementById("chunkSizeInput").value.trim() || "0,30,15");
      form.append("encoder_chunk_look_back", "4");
      form.append("decoder_chunk_look_back", "1");
      const response = await fetch("/v1/funasr/streaming", { method: "POST", body: form });
      if (!response.ok) throw new Error(await response.text());
      const payload = await response.json();
      sent += 1; sentValue.textContent = String(sent);
      if (Object.prototype.hasOwnProperty.call(payload, "full_text")) {
        transcriptBox.value = payload.full_text;
        updateTranscriptDownload();
      }
      if (payload.text) {
        log(`识别：${payload.text}`);
      } else if (sent % 5 === 0) {
        log(`已发送 ${sent} 个分片，后端暂未返回文字。`);
      }
    }
    async function flushChunks(isFinal=false) {
      const samplesPerChunk = currentChunkSamples();
      while (pcmBuffer.length >= samplesPerChunk || (isFinal && pcmBuffer.length > 0)) {
        const size = isFinal && pcmBuffer.length < samplesPerChunk ? pcmBuffer.length : samplesPerChunk;
        const chunk = new Float32Array(pcmBuffer.splice(0, size));
        await postChunk(chunk, isFinal && pcmBuffer.length === 0);
      }
    }
    function queueFlush(isFinal=false) {
      flushPromise = flushPromise.catch(() => {}).then(() => flushChunks(isFinal));
      return flushPromise;
    }
    async function start() {
      if (starting) return;
      starting = true;
      await stop(false);
      await ensureModelReadyBeforeRecording();
      sessionId = uuid(); sent = 0; pcmBuffer = []; transcriptBox.value = ""; recordedChunks = []; running = true; flushPromise = Promise.resolve();
      updateTranscriptDownload();
      lastPeak = 0; lowSignalFrames = 0;
      try {
        const deviceId = deviceSelect.value;
        const constraints = { audio: deviceId ? { deviceId: { exact: deviceId }, echoCancellation: false, noiseSuppression: false, autoGainControl: false } : { echoCancellation: false, noiseSuppression: false, autoGainControl: false }, video: false };
        stream = await navigator.mediaDevices.getUserMedia(constraints);
        await refreshDevices();
        const activeDeviceId = stream.getAudioTracks()[0]?.getSettings?.().deviceId || "";
        if (activeDeviceId && [...deviceSelect.options].some(option => option.value === activeDeviceId)) {
          deviceSelect.value = activeDeviceId;
        }
        audioContext = new AudioContext();
        if (audioContext.state === "suspended") {
          await audioContext.resume();
        }
        source = audioContext.createMediaStreamSource(stream);
        processor = audioContext.createScriptProcessor(4096, 1, 1);
        processor.onaudioprocess = async (event) => {
          if (!running) return;
          if (startupWatchdog) {
            clearTimeout(startupWatchdog);
            startupWatchdog = null;
          }
          const input = event.inputBuffer.getChannelData(0);
          updateMeter(input);
          if (!autoRestarted && lowSignalFrames >= 18) {
            autoRestarted = true;
            setStatus("检测到启动后持续近静音，正在自动重建麦克风采集链路...");
            setTimeout(() => start().catch(error => setStatus(`自动重启失败：${error.message}`)), 0);
            return;
          }
          const down = downsample(input, audioContext.sampleRate);
          for (const v of down) pcmBuffer.push(v);
          queueFlush(false).catch(error => setStatus(`发送分片失败：${error.message}`));
        };
        source.connect(processor); processor.connect(audioContext.destination);
        mediaRecorder = new MediaRecorder(stream);
        mediaRecorder.ondataavailable = e => { if (e.data && e.data.size) recordedChunks.push(e.data); };
        mediaRecorder.onstop = () => {
          if (objectUrl) URL.revokeObjectURL(objectUrl);
          objectUrl = URL.createObjectURL(new Blob(recordedChunks, { type: mediaRecorder.mimeType || "audio/webm" }));
          downloadLink.href = objectUrl; downloadLink.download = `native-mic-${Date.now()}.webm`; downloadLink.setAttribute("aria-disabled", "false");
        };
        mediaRecorder.start(500);
        startButton.disabled = true; stopButton.disabled = false; downloadLink.setAttribute("aria-disabled", "true");
        setStatus("正在原生收声并实时识别。");
        startupWatchdog = setTimeout(() => {
          if (running && sent === 0 && !autoRestarted) {
            autoRestarted = true;
            setStatus("启动后没有收到音频回调，正在自动重建麦克风采集链路...");
            start().catch(error => setStatus(`自动重启失败：${error.message}`));
          }
        }, 1800);
      } finally {
        starting = false;
      }
    }
    async function stop(show=true) {
      running = false;
      if (startupWatchdog) { clearTimeout(startupWatchdog); startupWatchdog = null; }
      try { await flushPromise; if (pcmBuffer.length) await queueFlush(true); } catch (error) { log(`最终分片失败：${error.message}`); }
      if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
      if (processor) { processor.disconnect(); processor = null; }
      if (source) { source.disconnect(); source = null; }
      if (audioContext) { await audioContext.close(); audioContext = null; }
      if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
      startButton.disabled = !modelReady; stopButton.disabled = true;
      if (show) setStatus("已停止录制。");
    }
    refreshButton.onclick = () => refreshDevices(true).catch(e => setStatus(`刷新设备失败：${e.message}`));
    startButton.onclick = () => {
      autoRestarted = false;
      start().catch(e => setStatus(`启动失败：${formatBrowserError(e)}`));
    };
    stopButton.onclick = () => stop(true);
    deviceSelect.onchange = () => {
      if (running && !starting) {
        autoRestarted = false;
        setStatus("输入设备已切换，正在自动重启采集...");
        start().catch(e => setStatus(`切换设备失败：${formatBrowserError(e)}`));
      }
    };
    document.getElementById("modelInput").onchange = () => {
      setModelReady(false);
      preloadSelectedModel().catch(e => setStatus(`模型加载失败：${e.message}`));
    };
    window.addEventListener("beforeunload", () => {
      if (stream) stream.getTracks().forEach(t => t.stop());
      if (transcriptObjectUrl) URL.revokeObjectURL(transcriptObjectUrl);
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    });
    refreshDevices(true).catch(e => setStatus(`初始化失败：${e.message}`));
    preloadSelectedModel().catch(e => setStatus(`模型加载失败：${e.message}`));
  </script>
</body>
</html>
"""


# 不同模型的默认 batch_size_s（秒），控制 FunASR AutoModel 内部 VAD 后逐段打包的总时长预算。
# 原理：FunASR AutoModel 挂载 fsmn-vad 时走 inference_with_vad() 路由，
#       VAD 先把长音频切成 ~30s 语音段，再按 batch_size_s 预算把若干段打包成 batch 推理。
#       batch_size_s 太小 → 段太多 → forward 次数爆炸（之前的双层分块问题）；
#       太大 → 单次 forward 内存压力大。
# 流式模型（paraformer-zh-streaming）排除——流式推理天然不需要批预算。
_ASR_DEFAULT_BATCH_S = {
    "qwen3-asr": 60,            # 高精度模型，适当放大 batch 预算减少 forward 次数
    "qwen3-asr-0.6b": 60,       # 轻量模型，60s 预算内存安全
    "sensevoice": 15,           # 非自回归快模型，batch 小些无妨
    "paraformer": 20,
    "paraformer-en": 20,
    "fun-asr-nano": 15,
}

def _default_batch_size_s(model: str) -> Optional[int]:
    """按模型返回默认 batch_size_s；流式/非 ASR 模型返回 None 让库自己处理。"""
    if model in _ASR_DEFAULT_BATCH_S:
        return _ASR_DEFAULT_BATCH_S[model]
    return None


# 运行时热词覆盖存储（5e 热词热更新）：model -> 热词字符串，免重启生效
_HOTWORD_OVERRIDES: dict[str, str] = {}
_HOTWORD_LOCK = threading.Lock()


def _effective_hotword(model: str, explicit: Optional[str]) -> Optional[str]:
    """解析生效热词：请求显式热词优先；否则用运行时覆盖（热更新）；都没有则 None。"""
    if explicit is not None:
        return explicit or None
    with _HOTWORD_LOCK:
        override = _HOTWORD_OVERRIDES.get(model, "")
    return override or None


def build_generate_kwargs(
    *,
    tmp_path: str,
    model: str,
    language: Optional[str],
    hotword: Optional[str],
    use_itn: Optional[bool],
    vad_preset: Optional[str],
    merge_vad: Optional[bool],
    merge_length_s: Optional[int],
    batch_size_s: Optional[int],
    batch_size_threshold_s: Optional[int],
    vad_max_single_segment_time: Optional[int],
    timestamp_level: str = "segment",
):
    """构建传给 FunASR generate() 的白名单参数。"""
    generate_kwargs = {"input": tmp_path, "batch_size": 1}
    normalized_lang = normalize_language(language)
    if normalized_lang:
        generate_kwargs["language"] = normalized_lang
    if hotword:
        generate_kwargs["hotword"] = hotword
    if use_itn is not None:
        generate_kwargs["use_itn"] = use_itn
    # 用户没显式传 batch_size_s 时，按模型给默认值，避免长音频过度分块
    if batch_size_s is None:
        batch_size_s = _default_batch_size_s(model)
    if batch_size_s is not None:
        if int(batch_size_s) <= 0:
            raise ValueError("batch_size_s must be > 0")
        generate_kwargs["batch_size_s"] = int(batch_size_s)
    if batch_size_threshold_s is not None:
        if int(batch_size_threshold_s) <= 0:
            raise ValueError("batch_size_threshold_s must be > 0")
        generate_kwargs["batch_size_threshold_s"] = int(batch_size_threshold_s)
    if timestamp_level != "off" and model in {"paraformer", "fun-asr-nano"}:
        generate_kwargs["sentence_timestamp"] = True
    if timestamp_level != "off" and model in {"qwen3-asr", "qwen3-asr-0.6b"}:
        generate_kwargs["output_timestamp"] = True
    return vad_presets.apply_vad_controls(
        generate_kwargs=generate_kwargs,
        vad_preset=vad_preset,
        merge_vad=merge_vad,
        merge_length_s=merge_length_s,
        vad_max_single_segment_time=vad_max_single_segment_time,
    )


def _safe_generate(asr_model, generate_kwargs: dict) -> list:
    """公共函数：调 FunASR generate()，自动处理 'timestamp' KeyError 回退。

    某些 model + batch_size_s 组合在传 sentence_timestamp=True 时会 KeyError，
    去掉 sentence_timestamp 重试即可。多处调用点都有同样的 try/except，统一在这里。
    """
    try:
        return asr_model.generate(**generate_kwargs)
    except KeyError as ke:
        if str(ke) == "'timestamp'" and "sentence_timestamp" in generate_kwargs:
            generate_kwargs.pop("sentence_timestamp", None)
            return asr_model.generate(**generate_kwargs)
        raise


def _run_asr(
    *,
    asr_model,
    source_path: str,
    generate_kwargs_base: dict,
    chunk_enabled: bool = False,
    chunk_seconds: float = 240,
    overlap_seconds: float = 10,
    total_duration_s: float = 0.0,
):
    """公共函数：执行 ASR，支持自动 chunking，返回 text/segments/elapsed/rtf。

    被 transcribe API（快速转录）和 _workflow_transcribe_model（精细转录）共用，
    彻底消除两套独立的 chunking + generate + elapsed 维护隐患。

    Returns:
        dict: {text, segments, elapsed_s, duration_s, rtf}
    """
    import time as _time
    t0 = _time.time()

    if chunk_enabled:
        # ---- 先尝试 ffmpeg 分块 ----
        from pat_funasr_webui.fine_transcription.transcription_pipeline import (
            _split_audio_ffmpeg,
            _merge_chunk_segments,
        )
        chunks = _split_audio_ffmpeg(
            source_path,
            chunk_seconds=chunk_seconds,
            overlap_seconds=overlap_seconds,
        )
        if not chunks:
            # 分块失败（ffmpeg 异常/源文件损坏等）：回退整文件单次识别，避免返回空结果
            logger.warning("ffmpeg 分块失败（chunks=0），回退整文件单次识别: %s", source_path)
            chunk_enabled = False

    if chunk_enabled:
        # ---- chunking 模式 ----
        all_segs: list[list[dict]] = []
        offsets: list[float] = []
        total_text_parts: list[str] = []
        for chunk_path, offset in chunks:
            ck = dict(generate_kwargs_base)
            ck["input"] = chunk_path
            cresult = _safe_generate(asr_model, ck)
            c0 = cresult[0] if cresult else {"text": ""}
            chunk_dur = segmentation.ffprobe_duration_s(chunk_path)
            seg = segmentation.build_segments(
                result0=c0, duration_s=chunk_dur, clean_text=clean_text
            )
            # 注意：此处不给 segments 加 offset，统一由 _merge_chunk_segments(offsets)
            # 内部按块偏移加上（含 start/end/words），避免双重偏移导致时间戳翻倍
            all_segs.append(seg)
            offsets.append(offset)
            total_text_parts.append(clean_text(c0.get("text", "")))
        segments = _merge_chunk_segments(all_segs, offsets, overlap_seconds=overlap_seconds)
        # 去重合并文本（重叠窗口内相同文本跳过）
        merged_parts: list[str] = []
        seen: dict[str, float] = {}
        for text_part, offset in zip(total_text_parts, offsets):
            short = text_part[:40].strip()
            if short and short in seen:
                continue
            if short:
                seen[short] = offset
            merged_parts.append(text_part)
        text = "".join(merged_parts)
    else:
        # ---- 单次 generate 模式 ----
        result = _safe_generate(asr_model, dict(generate_kwargs_base))
        result0 = result[0] if result else {"text": ""}
        text = clean_text(result0.get("text", ""))
        segments = segmentation.build_segments(
            result0=result0, duration_s=total_duration_s, clean_text=clean_text
        )
        if not segments:
            segments = [
                {"start": 0.0, "end": round(total_duration_s, 3), "text": text, "speaker": None}
            ]

    elapsed = _time.time() - t0
    duration_s = total_duration_s if total_duration_s > 0 else elapsed
    try:
        rtf = elapsed / duration_s if duration_s > 0 else 0.0
    except Exception:
        rtf = 0.0

    return {
        "text": text,
        "segments": segments,
        "elapsed_s": elapsed,
        "duration_s": duration_s,
        "rtf": rtf,
    }


def build_emotion_payload(
    *,
    model: str,
    granularity: str,
    result0: dict,
    meta: Optional[dict] = None,
) -> dict:
    """把 Emotion2vec 原始输出整理为稳定 JSON。"""
    labels = list(result0.get("labels") or [])
    scores = list(result0.get("scores") or [])
    emotions = []
    for label, score in zip(labels, scores):
        emotions.append({"label": str(label), "score": float(score)})
    emotions.sort(key=lambda item: item["score"], reverse=True)
    top = emotions[0] if emotions else {"label": "", "score": 0.0}
    payload = {
        "model": model,
        "granularity": granularity,
        "top_emotion": top["label"],
        "top_score": top["score"],
        "emotions": emotions,
    }
    if meta:
        payload["meta"] = meta
    return payload


def build_sensevoice_emotion_payload(
    *,
    model: str,
    raw_text: str,
    meta: Optional[dict] = None,
) -> dict:
    """把 SenseVoice 文本里的情感标签整理为情感结果 JSON。"""
    text = str(raw_text or "")
    # SenseVoice 实际返回里可能是 "<|HAPPY|>"，也可能是带空格的 "< | HAPPY | >"。
    raw_tokens = re.findall(r"<\s*\|\s*([^|>]+?)\s*\|\s*>", text)
    normalized_emotions = []
    for token in raw_tokens:
        normalized_token = re.sub(r"\s+", "", str(token or "")).upper()
        if normalized_token in {"HAPPY", "SAD", "ANGRY", "NEUTRAL"}:
            normalized_emotions.append(normalized_token.lower())
    top_emotion = normalized_emotions[0] if normalized_emotions else ""
    payload = {
        "model": model,
        "granularity": "utterance",
        "top_emotion": top_emotion,
        "top_score": 1.0 if top_emotion else 0.0,
        "emotions": ([{"label": top_emotion, "score": 1.0}] if top_emotion else []),
        "text": clean_text(text),
    }
    if meta:
        payload["meta"] = meta
    return payload


def build_diarization_payload(
    *,
    model: str,
    spk_model: str,
    spk_mode: str,
    result0: dict,
    duration_s: float,
) -> dict:
    """把说话人分离结果整理为稳定 JSON。"""
    text = clean_text(result0.get("text", ""))
    segments = segmentation.build_segments(
        result0=result0,
        duration_s=duration_s,
        clean_text=clean_text,
    )
    speakers = sorted({seg.get("speaker") for seg in segments if seg.get("speaker") is not None})
    return {
        "text": text,
        "segments": segments,
        "speakers": speakers,
        "model": model,
        "spk_model": spk_model,
        "spk_mode": spk_mode,
        "duration": round(duration_s, 3),
    }


def resolve_diarization_spk_mode(model: str, requested_spk_mode: str) -> str:
    """为不同模型选择更稳妥的说话人分离模式。"""
    normalized_mode = str(requested_spk_mode or "punc_segment")
    if model == "sensevoice" and normalized_mode == "punc_segment":
        return "vad_segment"
    return normalized_mode


def _workflow_transcribe_model(
    source_path: str,
    model_config: workflow_service.ModelRunConfig,
    config: workflow_service.WorkflowConfig,
    progress_callback,
) -> dict:
    """复用模型加载、参数白名单和分段器执行工作流中的一次 ASR。"""
    load_kwargs = {"punc_mode": model_config.punc_mode}
    # 空 asr_model = 让 primary 模型（config.transcription.primary.model）承担 diarization
    # 非空 = 只对指定模型启用（旧行为，兼容显式配置）
    target_asr = config.diarization.asr_model or config.transcription.primary.model
    reuse_for_diarization = (
        config.diarization.enabled
        and model_config.model == target_asr
    )
    effective_spk_mode = ""
    if reuse_for_diarization:
        effective_spk_mode = resolve_diarization_spk_mode(
            model_config.model,        # ← 用实际在跑的模型名，不是 config 里的字符串
            config.diarization.spk_mode,
        )
        load_kwargs["spk_model"] = config.diarization.speaker_model
        if model_config.model == "sensevoice" and effective_spk_mode == "vad_segment":
            load_kwargs["punc_mode"] = "disabled"
    if (
        config.timestamps.forced_alignment
        and MODEL_CAPABILITIES.get(model_config.model, {}).get("forced_alignment", False)
    ):
        load_kwargs["forced_aligner"] = config.timestamps.aligner_model
    asr_model = load_model(model_config.model, **load_kwargs)
    chunks: list[tuple[str, float]] = [(source_path, 0.0)]
    chunk_dirs: set[str] = set()
    if config.segmentation.chunk_enabled:
        from pat_funasr_webui.fine_transcription.transcription_pipeline import (
            _split_audio_ffmpeg,
        )

        try:
            split_chunks = _split_audio_ffmpeg(
                source_path,
                chunk_seconds=config.segmentation.chunk_seconds,
                overlap_seconds=config.segmentation.overlap_seconds,
            )
            if split_chunks:
                chunks = split_chunks
                chunk_dirs = {str(Path(path).parent) for path, _offset in chunks}
                logger.info(f"Audio chunking succeeded: {len(chunks)} chunks")
            else:
                logger.warning("Audio chunking enabled but ffmpeg returned no chunks, fallback to single pass")
        except Exception as exc:
            logger.warning(f"Audio chunking failed ({exc}), fallback to single pass")

    all_segments: list[list[dict]] = []
    all_words: list[dict] = []
    offsets: list[float] = []
    total_duration = 0.0
    try:
        for index, (chunk_path, offset) in enumerate(chunks, start=1):
            generate_kwargs = build_generate_kwargs(
                tmp_path=chunk_path,
                model=model_config.model,
                language=model_config.language,
                hotword=_effective_hotword(model_config.model, model_config.hotword or None),
                use_itn=model_config.use_itn,
                vad_preset=config.segmentation.vad_preset if config.segmentation.vad_enabled else None,
                merge_vad=None,
                merge_length_s=None,
                batch_size_s=None,
                batch_size_threshold_s=None,
                vad_max_single_segment_time=None,
                timestamp_level=config.timestamps.level,
            )
            if reuse_for_diarization:
                generate_kwargs.update(
                    {
                        "spk_mode": effective_spk_mode,
                        "return_spk_res": True,
                        "output_timestamp": True,
                    }
                )
                if config.diarization.preset_speaker_count is not None:
                    generate_kwargs["preset_spk_num"] = int(
                        config.diarization.preset_speaker_count
                    )
            generated = _safe_generate(asr_model, generate_kwargs)
            result0 = generated[0] if generated else {"text": ""}
            duration_s = segmentation.ffprobe_duration_s(chunk_path)
            text = clean_text(result0.get("text", ""))
            segments = segmentation.build_segments(
                result0=result0,
                duration_s=duration_s,
                clean_text=clean_text,
            )
            if not segments:
                segments = [
                    {
                        "start": 0.0,
                        "end": round(duration_s, 3),
                        "text": text,
                        "speaker": None,
                    }
                ]
            all_segments.append(segments)
            if config.timestamps.level == "word":
                for word in result0.get("timestamps") or []:
                    if not isinstance(word, dict):
                        continue
                    start = word.get("start_time", word.get("start"))
                    end = word.get("end_time", word.get("end"))
                    if start is None or end is None:
                        continue
                    all_words.append(
                        {
                            "word": str(word.get("text") or word.get("word") or ""),
                            "start": round(float(start) + offset, 3),
                            "end": round(float(end) + offset, 3),
                        }
                    )
            offsets.append(offset)
            total_duration = max(total_duration, offset + duration_s)
            progress_callback(index, len(chunks), f"分块 {index}/{len(chunks)} 转录完成")
    finally:
        for directory in chunk_dirs:
            shutil.rmtree(directory, ignore_errors=True)

    if len(all_segments) == 1:
        merged_segments = all_segments[0]
    else:
        from pat_funasr_webui.fine_transcription.transcription_pipeline import (
            _merge_chunk_segments,
        )

        merged_segments = _merge_chunk_segments(
            all_segments,
            offsets,
            overlap_seconds=config.segmentation.overlap_seconds,
        )
    output = {
        "model": model_config.model,
        "weight": model_config.weight,
        "language": model_config.language,
        "duration": round(total_duration, 3),
        "text": "".join(str(item.get("text") or "") for item in merged_segments),
        "segments": merged_segments,
        "chunks": len(chunks),
        "timestamp_level": config.timestamps.level,
    }
    if config.timestamps.level == "word":
        deduplicated_words: list[dict] = []
        seen_words: set[tuple] = set()
        for word in sorted(all_words, key=lambda item: (item["start"], item["end"])):
            key = (word["word"], word["start"], word["end"])
            if key not in seen_words:
                seen_words.add(key)
                deduplicated_words.append(word)
        output["words"] = deduplicated_words
    if reuse_for_diarization:
        diarization_segments = [
            {
                key: value
                for key, value in segment.items()
                if key in {"start", "end", "speaker", "text"}
            }
            for segment in merged_segments
            if segment.get("speaker") is not None
        ]
        output["diarization"] = {
            "text": output["text"],
            "segments": diarization_segments,
            "speakers": sorted(
                {
                    item.get("speaker")
                    for item in diarization_segments
                    if item.get("speaker") is not None
                }
            ),
            "model": model_config.model,
            "spk_model": config.diarization.speaker_model,
            "spk_mode": effective_spk_mode,
            "duration": output["duration"],
        }
    return output


def _workflow_preprocess(source_path: str, preprocess_config, output_dir: str) -> str:
    """按显式配置执行 FFmpeg 前处理，并把结果保留在任务目录。"""
    from pat_funasr_webui.fine_transcription.audio_processor import process_audio

    destination = Path(output_dir).resolve().parent / "processed.wav"
    processed_path, _before, _after = process_audio(
        source_path,
        noise_reduction=preprocess_config.noise_reduction,
        noise_strength=preprocess_config.noise_strength,
        sample_rate=preprocess_config.sample_rate,
        vad_enabled=preprocess_config.silence_mode == "trim_silence",
        loudnorm=preprocess_config.loudnorm,
        output_path=str(destination),
    )
    return str(processed_path)


def _workflow_diarize(source_path: str, diarization_config) -> dict:
    """生成独立说话人时间轴，后续只按时间重叠对齐到主转录。"""
    effective_spk_mode = resolve_diarization_spk_mode(
        diarization_config.asr_model,
        diarization_config.spk_mode,
    )
    load_kwargs = {"spk_model": diarization_config.speaker_model}
    if diarization_config.asr_model == "sensevoice" and effective_spk_mode == "vad_segment":
        load_kwargs["punc_mode"] = "disabled"
    asr_model = load_model(diarization_config.asr_model, **load_kwargs)
    # diarization 模型也是 ASR 模型，同样需要合理的 chunk 大小
    generate_kwargs = {
        "input": source_path,
        "batch_size": 1,
        "spk_mode": effective_spk_mode,
        "return_spk_res": True,
        "output_timestamp": True,
    }
    _bs = _default_batch_size_s(diarization_config.asr_model)
    if _bs is not None:
        generate_kwargs["batch_size_s"] = _bs
    if diarization_config.preset_speaker_count is not None:
        generate_kwargs["preset_spk_num"] = int(diarization_config.preset_speaker_count)
    generated = asr_model.generate(**generate_kwargs)
    return build_diarization_payload(
        model=diarization_config.asr_model,
        spk_model=diarization_config.speaker_model,
        spk_mode=effective_spk_mode,
        result0=generated[0] if generated else {},
        duration_s=segmentation.ffprobe_duration_s(source_path),
    )


def _resolve_workflow_llm(stage_config):
    """将前端选择的 provider profile 与模型解析为可调用配置。"""
    from pat_funasr_webui.fine_transcription.llm_config import get_llm_by_value

    profile_value = str(stage_config.provider_profile_id or "")
    selection = profile_value if "|" in profile_value else f"{profile_value}|{stage_config.model}"
    resolved = get_llm_by_value(selection)
    if resolved is None:
        raise RuntimeError(f"LLM provider profile 不存在或模型未启用：{selection}")
    llm_config, selected_model = resolved
    if selected_model != stage_config.model:
        raise RuntimeError("LLM provider profile 与所选模型不一致")
    return llm_config, selected_model


def _workflow_llm_stage(stage_name: str, text: str, stage_config):
    """调用已配置的 LLM 执行校对、纪要或思维导图。"""
    from pat_funasr_webui.fine_transcription.summary_processor import (
        generate_mindmap,
        generate_summary,
        refine_transcript,
    )

    llm_config, selected_model = _resolve_workflow_llm(stage_config)
    common = {
        "base_url": llm_config.base_url,
        "api_key": llm_config.api_key or "no-key",
        "model": selected_model,
    }
    template_id = str(stage_config.template_id or "default")
    if stage_name == "llm_proofread":
        prompts = {
            "default": "只校正错别字、同音词、标点和断句，不增删事实，不输出说明。",
            "strict": "严格逐句校正错别字、同音词、标点和断句；保留原意、数字、专名和语气，不补充内容，不输出说明。",
            "meeting": "校正会议转写中的错别字、专名、标点和断句；保留每项事实、数字、决定和行动项，不输出说明。",
        }
        prompt = prompts[template_id]
        return refine_transcript(text, prompt, **common)
    if stage_name == "summary":
        prompts = {
            "default": _SUMMARY_PROMPT_DEFAULT,
            "strict": _SUMMARY_PROMPT_STRICT,
            "meeting": _SUMMARY_PROMPT_MEETING,
        }
        prompt = prompts[template_id]
        return generate_summary(text, prompt, **common)
    if stage_name == "mindmap":
        _MM_BASE = "从以下带说话人标签的转写生成 JSON 思维导图，格式为 {title, children:[...]}，层级不超过 3 级。仅输出 JSON。"
        _MM_MEETING = "根据会议转写按议题、决定和行动项生成 JSON 思维导图。"
        _MM_STRICT = "仅依据转写生成 JSON 思维导图，不补充未知事实。"
        prompts = {
            "default": _MM_BASE,
            "strict": _MM_STRICT + " " + _MM_BASE,
            "meeting": _MM_MEETING + " " + _MM_BASE,
        }
        prompt = prompts[template_id]
        return generate_mindmap(text, prompt, **common)
    raise RuntimeError(f"未知 LLM 阶段：{stage_name}")


def _workflow_translate(text: str, translation_config):
    """
    翻译全文：NLLB translate 内部已自动按 ≤500 字分块（按句号/感叹号/问号/换行切分），
    直接传全文即可，短文本自动透传，避免 max_length=512 截断。
    """
    model_obj = load_model(translation_config.model)
    return model_obj.translate(
        text,
        translation_config.source_lang,
        translation_config.target_lang,
    )


def _workflow_emotion(source_path: str, emotion_config) -> dict:
    emotion_model = load_model(emotion_config.model)
    generated = emotion_model.generate(
        input=source_path,
        granularity=emotion_config.granularity,
        extract_embedding=False,
    )
    result0 = generated[0] if generated else {}
    if emotion_config.model == "sensevoice":
        return build_sensevoice_emotion_payload(
            model=emotion_config.model,
            raw_text=str(result0.get("text", "") or ""),
        )
    return build_emotion_payload(
        model=emotion_config.model,
        granularity=emotion_config.granularity,
        result0=result0,
    )


WORKFLOW_RUNTIME = workflow_runner.WorkflowRuntime(
    transcribe=_workflow_transcribe_model,
    preprocess=_workflow_preprocess,
    diarize=_workflow_diarize,
    llm_stage=_workflow_llm_stage,
    translate=_workflow_translate,
    emotion=_workflow_emotion,
    write_artifacts=artifact_service.write_workflow_artifacts,
)


def _run_workflow_job(context: workflow_service.WorkflowRunContext) -> dict:
    """API 默认真实工作流执行器。"""
    return workflow_runner.run_workflow(context, WORKFLOW_RUNTIME)


WORKFLOW_RUNNER = _run_workflow_job


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(default="sensevoice"),
    language: Optional[str] = Form(default=None),
    response_format: Optional[str] = Form(default="json"),
    max_line_width: Optional[int] = Form(default=None),
    vad_preset: Optional[str] = Form(default=None),
    vad_max_single_segment_time: Optional[int] = Form(default=None),
    merge_vad: Optional[bool] = Form(default=None),
    merge_length_s: Optional[int] = Form(default=None),
    hotword: Optional[str] = Form(default=None),
    use_itn: Optional[bool] = Form(default=None),
    batch_size_s: Optional[int] = Form(default=None),
    batch_size_threshold_s: Optional[int] = Form(default=None),
    punc_mode: Optional[str] = Form(default="auto"),
    device: Optional[str] = Form(default=None),
    hub: Optional[str] = Form(default=None),
    disable_update: Optional[bool] = Form(default=None),
    ncpu: Optional[int] = Form(default=None),
    log_level: Optional[str] = Form(default=None),
    disable_pbar: Optional[bool] = Form(default=None),
    # 长音频分块控制（可选，默认自动判断：>5min 自动启用）
    chunk_enabled: Optional[bool] = Form(default=None),
    chunk_seconds: Optional[int] = Form(default=None),
    overlap_seconds: Optional[int] = Form(default=None),
):
    """
    OpenAI-compatible audio transcription endpoint.
    
    Accepts the same parameters as OpenAI's /v1/audio/transcriptions:
    - file: Audio file (wav, mp3, flac, m4a, ogg, webm)
    - model: Model to use (sensevoice, paraformer, fun-asr-nano)
    - language: Optional language hint
    - response_format: json/verbose_json/txt/srt/vtt/tsv/all
    - vad_preset: default/anti_hallucination（可选）
    - vad_max_single_segment_time: 单段最大时长（毫秒，可选）
    - merge_vad: true/false（可选，优先级高于 preset）
    - merge_length_s: 合并段长度（秒，可选，需要配合 merge_vad=true）
    - hotword: 热词字符串（可选，逗号/空格分隔）
    - use_itn: 是否开启逆文本正规化（可选）
    - batch_size_s: 动态批总时长（秒，可选）
    - batch_size_threshold_s: 长音频动态批阈值（秒，可选，用于降低 OOM 风险）
    - punc_mode: auto/disabled（可选；disabled 仅对外置 PUNC 模型生效）
    - device/hub/disable_update/ncpu/log_level/disable_pbar: AutoModel 运行时控制项（可选）
    """
    # Validate model
    if model not in MODEL_CONFIGS:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model}' not found. Available: {list(MODEL_CONFIGS.keys())}"
        )
    if model not in OFFLINE_ASR_MODELS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Model '{model}' does not support offline transcription. "
                f"Offline ASR models: {sorted(OFFLINE_ASR_MODELS)}"
            ),
        )

    allowed_formats = {"json", "verbose_json", "txt", "srt", "vtt", "tsv", "csv", "docx", "all"}
    if response_format not in allowed_formats:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported response_format '{response_format}'. Allowed: {sorted(allowed_formats)}",
        )

    if vad_preset is not None and vad_preset not in vad_presets.allowed_presets():
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported vad_preset '{vad_preset}'. Allowed: {sorted(vad_presets.allowed_presets())}",
        )
    if punc_mode is not None and punc_mode not in VALID_PUNC_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported punc_mode '{punc_mode}'. Allowed: {sorted(VALID_PUNC_MODES)}",
        )

    # 分块保存上传文件，在写入过程中执行上限，避免整文件先进入内存。
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            tmp_path, upload_bytes = await media_service.save_upload_file(
                file,
                tmpdir,
                max_bytes=MAX_UPLOAD_BYTES,
            )
        except media_service.UploadTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"读取上传文件失败：{exc}") from exc

        try:
            try:
                asr_model = await run_in_threadpool(
                    partial(
                        load_model,
                        model,
                        device=device,
                        hub=hub,
                        disable_update=disable_update,
                        ncpu=ncpu,
                        log_level=log_level,
                        disable_pbar=disable_pbar,
                        punc_mode=punc_mode,
                    )
                )
            except ValueError as ve:
                raise HTTPException(status_code=400, detail=str(ve))
            t0 = time.time()

            duration_s = segmentation.ffprobe_duration_s(tmp_path)
            try:
                generate_kwargs = build_generate_kwargs(
                    tmp_path=tmp_path,
                    model=model,
                    language=language,
                    hotword=_effective_hotword(model, hotword),
                    use_itn=use_itn,
                    vad_preset=vad_preset,
                    merge_vad=merge_vad,
                    merge_length_s=merge_length_s,
                    batch_size_s=batch_size_s,
                    batch_size_threshold_s=batch_size_threshold_s,
                    vad_max_single_segment_time=vad_max_single_segment_time,
                )
            except ValueError as ve:
                raise HTTPException(status_code=400, detail=str(ve))

            logger.info(
                "Transcription start: "
                f"model={model}, "
                f"language={language or 'auto'}, "
                f"vad_preset={vad_preset or ''}, "
                f"merge_vad={bool(merge_vad) if merge_vad is not None else ''}, "
                f"batch_size_s={batch_size_s or ''}, "
                f"batch_size_threshold_s={batch_size_threshold_s or ''}, "
                f"file={getattr(file, 'filename', '')}"
            )

            # ---- 统一用公共函数 _run_asr（transcribe API 和 workflow 共用）----
            _chunk_seconds = chunk_seconds if chunk_seconds is not None else 240
            _overlap = overlap_seconds if overlap_seconds is not None else 10

            # 参数校验：对齐 workflow 校验规则（segmentation.overlap_seconds >= chunk_seconds 时报错）
            if _chunk_seconds <= 0:
                raise HTTPException(status_code=400, detail="chunk_seconds 必须大于 0")
            if _overlap < 0:
                raise HTTPException(status_code=400, detail="overlap_seconds 不能为负")
            if _overlap >= _chunk_seconds:
                raise HTTPException(status_code=400, detail="块间重叠秒数必须小于每块秒数")

            # ---- 外层 ffmpeg chunking 自动判定 ----
            # 外层 chunking 是「兜底机制」，只在模型自身无法处理长音频时才启用。
            #
            # 各模型原生长音频能力（基于官方文档 + 源码验证）：
            #
            #  qwen3-asr / qwen3-asr-0.6b：
            #     qwen-asr 包原生 split_audio_into_chunks(MAX_ASR_INPUT_SECONDS=1200s, 静音边界切块)
            #     + MODEL_CONFIGS 已配置 vad_model=fsmn-vad（FunASR VAD 先切 ~30s 段，
            #       避免 max_new_tokens=512 截断），与 model_catalog 注释一致
            #     → 外层 chunking **默认完全关闭**
            #
            #  paraformer-zh-streaming：
            #     流式模型，天然支持任意长度音频，硬切会破坏流式语义
            #     → 外层 chunking **强制关闭**
            #
            #  sensevoice / paraformer / paraformer-en / fun-asr-nano：
            #     FunASR AutoModel 挂载 fsmn-vad + batch_size_s 控制分块
            #     一般长音频（< 90min）AutoModel 能扛住，极端长时外层兜底避免 OOM
            #     → 外层 chunking **仅 > 5400s (90min) 才启用**
            #
            # 调用方显式传 chunk_enabled=true/false 时优先尊重。
            _NATIVE_CHUNK_MODELS = {"qwen3-asr", "qwen3-asr-0.6b"}  # 包原生静音边界切块
            _STREAMING_MODELS = {"paraformer-zh-streaming"}
            if chunk_enabled is not None:
                _auto_chunk = chunk_enabled                  # 用户显式指定 → 尊重
            elif model in _STREAMING_MODELS:
                _auto_chunk = False                          # 流式 → 永远不切
            elif model in _NATIVE_CHUNK_MODELS:
                _auto_chunk = False                          # 原生分块 → 外层不干预
            else:
                _auto_chunk = duration_s > 5400              # 其他 VAD+batch 模型：>90min 兜底

            # generate_kwargs 已有 "input": tmp_path，chunking 时 _run_asr 会覆盖为 chunk_path
            _run_result = await run_in_threadpool(
                partial(
                    _run_asr,
                    asr_model=asr_model,
                    source_path=tmp_path,
                    generate_kwargs_base=generate_kwargs,
                    chunk_enabled=_auto_chunk,
                    chunk_seconds=_chunk_seconds,
                    overlap_seconds=_overlap,
                    total_duration_s=duration_s,
                )
            )
            text = _run_result["text"]
            segments = _run_result["segments"]
            elapsed = _run_result["elapsed_s"]
            duration_s = _run_result["duration_s"]
            rtf = _run_result["rtf"]

            logger.info(
                "Transcription done: "
                f"model={model}, "
                f"elapsed_s={elapsed:.2f}, "
                f"duration_s={duration_s:.2f}, "
                f"rtf={rtf:.3f}"
            )
            if _auto_chunk:
                logger.info(f"Auto chunk: duration={duration_s:.1f}s → chunking mode enabled")

            verbose_payload = renderers.build_verbose_json_payload(
                full_text=text,
                segments=segments,
                meta={
                    "language": language or "auto",
                    "duration": round(duration_s, 3),
                    "model": model,
                },
            )

            if response_format == "json":
                return JSONResponse({"text": text})
            if response_format == "verbose_json":
                return JSONResponse(verbose_payload)
            if response_format == "txt":
                resp_content = renderers.render_txt(segments, max_line_width=max_line_width)
                return Response(content=resp_content, media_type="text/plain; charset=utf-8")
            if response_format == "tsv":
                resp_content = renderers.render_tsv(segments)
                return Response(content=resp_content, media_type="text/tab-separated-values; charset=utf-8")
            if response_format == "srt":
                resp_content = renderers.render_srt(segments, max_line_width=max_line_width)
                return Response(content=resp_content, media_type="application/x-subrip; charset=utf-8")
            if response_format == "vtt":
                resp_content = renderers.render_vtt(segments, max_line_width=max_line_width)
                return Response(content=resp_content, media_type="text/vtt; charset=utf-8")
            if response_format == "csv":
                resp_content = renderers.render_csv(segments)
                return Response(content=resp_content, media_type="text/csv; charset=utf-8")
            if response_format == "docx":
                resp_content = renderers.render_docx(segments)
                headers = {"Content-Disposition": 'attachment; filename="transcript.docx"'}
                return Response(
                    content=resp_content,
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers=headers,
                )
            if response_format == "all":
                _ts = artifact_service._make_timestamp()
                zbytes = renderers.render_all_zip(
                    full_text=text,
                    segments=segments,
                    json_payload=verbose_payload,
                    max_line_width=max_line_width,
                    timestamp=_ts,
                )
                headers = {"Content-Disposition": f'attachment; filename="transcript_{_ts}.zip"'}
                return Response(content=zbytes, media_type="application/zip", headers=headers)

            return JSONResponse({"text": text})

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/funasr/streaming")
async def transcribe_streaming(
    file: UploadFile = File(...),
    model: str = Form(default="paraformer-zh-streaming"),
    session_id: Optional[str] = Form(default=None),
    reset: Optional[bool] = Form(default=False),
    is_final: Optional[bool] = Form(default=False),
    chunk_size: str = Form(default="0,10,5"),
    encoder_chunk_look_back: int = Form(default=4),
    decoder_chunk_look_back: int = Form(default=1),
):
    if model not in MODEL_CONFIGS:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model}' not found. Available: {list(MODEL_CONFIGS.keys())}",
        )
    if model not in STREAMING_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model}' does not support streaming. Streaming models: {sorted(STREAMING_MODELS)}",
        )

    try:
        chunk = await media_service.read_upload_bytes_limited(
            file,
            max_bytes=MAX_STREAM_CHUNK_BYTES,
        )
    except media_service.UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取上传分片失败：{exc}") from exc
    if not chunk:
        raise HTTPException(status_code=400, detail="上传分片为空")

    try:
        parsed_chunk_size = _parse_chunk_size(chunk_size)
        now = time.time()
        _cleanup_streaming_sessions(now)
        sid, state = _get_or_create_streaming_session(
            session_id=session_id,
            model=model,
            reset=bool(reset),
            now=now,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        asr_model = await run_in_threadpool(load_model, model)
        speech_chunk = pcm16_bytes_to_float32_audio(chunk)
        # per-session 锁：同一会话的分片必须串行处理（cache 增量 + full_text 合并），
        # 否则并发请求会互相覆盖 cache 导致流式结果错乱
        session_lock = state.get("lock") or threading.Lock()
        with session_lock:
            result = await run_in_threadpool(
                partial(
                    asr_model.generate,
                    input=speech_chunk,
                    cache=state["cache"],
                    is_final=bool(is_final),
                    chunk_size=parsed_chunk_size,
                    encoder_chunk_look_back=int(encoder_chunk_look_back),
                    decoder_chunk_look_back=int(decoder_chunk_look_back),
                )
            )
            text = clean_text(result[0].get("text", "")) if result else ""
            state["full_text"] = merge_streaming_text(state.get("full_text", ""), text)
            state["updated_at"] = time.time()
        return JSONResponse(
            {
                "session_id": sid,
                "text": text,
                "full_text": state["full_text"],
                "is_final": bool(is_final),
            }
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Streaming transcription error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/v1/funasr/emotion")
async def recognize_emotion(
    file: UploadFile = File(...),
    model: str = Form(default="emotion2vec-plus-large"),
    granularity: str = Form(default="utterance"),
):
    """情感识别接口。"""
    if model not in MODEL_CONFIGS:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model}' not found. Available: {list(MODEL_CONFIGS.keys())}",
        )
    if model not in EMOTION_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model}' does not support emotion recognition. Emotion models: {sorted(EMOTION_MODELS)}",
        )
    if model == "sensevoice" and granularity != "utterance":
        raise HTTPException(
            status_code=400,
            detail="Model 'sensevoice' only supports granularity='utterance' for emotion recognition",
        )
    if granularity not in {"utterance", "frame"}:
        raise HTTPException(
            status_code=400,
            detail="Unsupported granularity. Allowed: ['frame', 'utterance']",
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            tmp_path, _upload_bytes = await media_service.save_upload_file(
                file,
                tmpdir,
                max_bytes=MAX_UPLOAD_BYTES,
            )
        except media_service.UploadTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"读取上传文件失败：{exc}") from exc

        try:
            emotion_model = await run_in_threadpool(load_model, model)
            result = await run_in_threadpool(
                partial(
                    emotion_model.generate,
                    input=tmp_path,
                    granularity=granularity,
                    extract_embedding=False,
                )
            )
            result0 = result[0] if result else {}
            if model == "sensevoice":
                payload = build_sensevoice_emotion_payload(
                    model=model,
                    raw_text=str(result0.get("text", "") or ""),
                )
            else:
                payload = build_emotion_payload(
                    model=model,
                    granularity=granularity,
                    result0=result0,
                )
            return JSONResponse(payload)
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Emotion recognition error: {exc}")
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/v1/funasr/diarization")
async def recognize_diarization(
    file: UploadFile = File(...),
    model: str = Form(default="paraformer"),
    spk_model: str = Form(default="cam++"),
    spk_mode: str = Form(default="punc_segment"),
    preset_spk_num: Optional[int] = Form(default=None),
):
    """说话人分离接口。"""
    if model not in MODEL_CONFIGS:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model}' not found. Available: {list(MODEL_CONFIGS.keys())}",
        )
    if model not in DIARIZATION_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model}' does not support diarization. Diarization models: {sorted(DIARIZATION_MODELS)}",
        )
    if spk_mode not in {"default", "vad_segment", "punc_segment"}:
        raise HTTPException(
            status_code=400,
            detail="Unsupported spk_mode. Allowed: ['default', 'punc_segment', 'vad_segment']",
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            tmp_path, _upload_bytes = await media_service.save_upload_file(
                file,
                tmpdir,
                max_bytes=MAX_UPLOAD_BYTES,
            )
        except media_service.UploadTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"读取上传文件失败：{exc}") from exc

        try:
            duration_s = segmentation.ffprobe_duration_s(tmp_path)
            effective_spk_mode = resolve_diarization_spk_mode(model, spk_mode)
            load_kwargs = {"spk_model": spk_model}
            # SenseVoice 说话人分离在 vad_segment 下仍加载 punc_model 时，可能触发时间戳异常。
            if model == "sensevoice" and effective_spk_mode == "vad_segment":
                load_kwargs["punc_mode"] = "disabled"
            asr_model = await run_in_threadpool(partial(load_model, model, **load_kwargs))
            # diarization 模型也是 ASR 模型，同样需要合理的 chunk 大小
            generate_kwargs = {
                "input": tmp_path,
                "batch_size": 1,
                "spk_mode": effective_spk_mode,
                "return_spk_res": True,
                "output_timestamp": True,
            }
            _bs = _default_batch_size_s(model)
            if _bs is not None:
                generate_kwargs["batch_size_s"] = _bs
            if preset_spk_num is not None:
                generate_kwargs["preset_spk_num"] = int(preset_spk_num)
            result = await run_in_threadpool(partial(asr_model.generate, **generate_kwargs))
            result0 = result[0] if result else {}
            payload = build_diarization_payload(
                model=model,
                spk_model=spk_model,
                spk_mode=effective_spk_mode,
                result0=result0,
                duration_s=duration_s,
            )
            return JSONResponse(payload)
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Diarization error: {exc}")
            raise HTTPException(status_code=500, detail=str(exc)) from exc


def _parse_workflow_payload(raw: str | dict) -> workflow_service.WorkflowConfig:
    """解析 HTTP 请求中的 workflow JSON，并自动推断强制对齐配置。"""
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"workflow JSON 解析失败：{exc.msg}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="workflow JSON 必须是对象")
    config = workflow_service.parse_workflow_config(payload)
    # 自动补全强制对齐：字词级时间戳 → forced_alignment=True + 默认模型
    workflow_service.auto_fill_forced_alignment(config)
    # 自动补全转录模式：reviewers 非空 → multi_model
    workflow_service.auto_fill_transcription_mode(config)
    return config


@app.post("/v1/funasr/workflows/validate")
async def validate_workflow(payload: dict):
    """校验工作流 schema、模型能力与跨阶段依赖。"""
    try:
        config = workflow_service.parse_workflow_config(payload)
    except workflow_service.WorkflowConfigError as exc:
        return JSONResponse(
            {
                "valid": False,
                "errors": [
                    {
                        "code": "WORKFLOW_SCHEMA_INVALID",
                        "path": "workflow",
                        "message": str(exc),
                    }
                ],
                "warnings": [],
                "normalized": None,
            }
        )
    errors, warnings = _validate_workflow_with_local_models(config)
    return JSONResponse(
        {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "normalized": workflow_service.workflow_config_to_dict(config),
        }
    )


@app.post("/v1/funasr/workflows")
async def submit_workflow(
    file: UploadFile = File(...),
    workflow: str = Form(...),
):
    """提交显式精细转录工作流，返回异步任务 ID。"""
    config = _parse_workflow_payload(workflow)
    errors, warnings = _validate_workflow_with_local_models(config)
    if errors:
        raise HTTPException(
            status_code=400,
            detail={"message": "workflow 配置校验失败", "errors": errors, "warnings": warnings},
        )

    _cleanup_workflow_temp_dirs()
    upload_dir = WORKFLOW_TEMP_ROOT / f"pending_{uuid.uuid4().hex}"
    try:
        source_path, upload_bytes = await media_service.save_upload_file(
            file,
            upload_dir,
            max_bytes=MAX_UPLOAD_BYTES,
        )
    except media_service.UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取上传文件失败：{exc}") from exc

    config_snapshot = workflow_service.workflow_config_to_dict(config)
    job_id = WORKFLOW_MANAGER.submit(
        config=config_snapshot,
        source_path=source_path,
        runner=WORKFLOW_RUNNER,
    )
    return JSONResponse(
        {
            "job_id": job_id,
            "status": "queued",
            "upload_bytes": upload_bytes,
            "warnings": warnings,
            "status_url": f"/v1/funasr/workflows/{job_id}",
            "events_url": f"/v1/funasr/workflows/{job_id}/events",
        },
        status_code=202,
    )


@app.get("/v1/funasr/workflows")
async def list_workflows():
    """列出当前进程内工作流任务，供模型与服务页展示队列。"""
    WORKFLOW_MANAGER.prune_terminal_jobs()
    return JSONResponse({"object": "list", "data": WORKFLOW_MANAGER.list_snapshots()})


@app.get("/v1/funasr/workflows/{job_id}")
async def get_workflow(job_id: str):
    """获取任务和完整事件快照。"""
    try:
        return JSONResponse(
            WORKFLOW_MANAGER.get_snapshot(job_id, include_events=False)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Workflow job not found: {job_id}") from exc


@app.get("/v1/funasr/workflows/{job_id}/events")
async def get_workflow_events(job_id: str, after_event_id: int = 0):
    """获取指定事件之后的追加日志；前端可轮询，后续可复用为 SSE 数据源。"""
    try:
        event_snapshot = WORKFLOW_MANAGER.get_events(
            job_id,
            after_event_id=after_event_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Workflow job not found: {job_id}") from exc
    return JSONResponse(
        {
            "object": "list",
            "job_id": job_id,
            "status": event_snapshot["status"],
            "data": event_snapshot["events"],
        }
    )


async def _workflow_event_stream(job_id: str, after_event_id: int):
    """SSE 事件生成器：按增量轮询任务事件，终态后发送 done 事件并退出。

    客户端断开时 asyncio.sleep 抛 CancelledError，生成器随之退出。
    """
    last_id = max(0, int(after_event_id))
    while True:
        try:
            snapshot = WORKFLOW_MANAGER.get_events(job_id, after_event_id=last_id)
        except KeyError:
            # 任务在推送途中被清理（如 TTL prune），告知客户端后结束
            yield f'event: error\ndata: {json.dumps({"error": "job not found"}, ensure_ascii=False)}\n\n'
            return
        for event in snapshot["events"]:
            last_id = max(last_id, int(event.get("event_id", 0)))
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        if snapshot["status"] in workflow_service.WorkflowJobManager.TERMINAL_STATUSES:
            yield (
                "event: done\n"
                f"data: {json.dumps({'status': snapshot['status'], 'job_id': job_id}, ensure_ascii=False)}\n\n"
            )
            return
        # 心跳注释行，避免代理把长连接误判为超时
        yield ": keep-alive\n\n"
        await asyncio.sleep(0.5)


@app.get("/v1/funasr/workflows/{job_id}/events/stream")
async def stream_workflow_events(job_id: str, after_event_id: int = 0):
    """SSE 事件推送：任务事件增量实时下发，终态后发送 done 事件。

    与 /events 轮询端点共存：本端点仅新增推送通道，不改变轮询契约。
    """
    # 先校验任务存在，把 404 前置到响应头返回，而非流入 SSE 体
    try:
        WORKFLOW_MANAGER.get_events(job_id, after_event_id=after_event_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Workflow job not found: {job_id}") from exc
    return StreamingResponse(
        _workflow_event_stream(job_id, after_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/v1/funasr/workflows/{job_id}/cancel")
async def cancel_workflow(job_id: str):
    """请求取消工作流任务。"""
    try:
        return JSONResponse(WORKFLOW_MANAGER.cancel(job_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Workflow job not found: {job_id}") from exc


@app.get("/v1/funasr/workflows/{job_id}/artifacts/{artifact_name}")
async def download_workflow_artifact(job_id: str, artifact_name: str):
    """下载任务声明的产物；仅允许访问结果清单中的精确文件。"""
    try:
        snapshot = WORKFLOW_MANAGER.get_snapshot(job_id, include_internal=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Workflow job not found: {job_id}") from exc
    if Path(artifact_name).name != artifact_name:
        raise HTTPException(status_code=400, detail="Invalid artifact name")
    artifacts = (snapshot.get("result") or {}).get("artifacts") or []
    artifact = next((item for item in artifacts if item.get("name") == artifact_name), None)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_name}")
    path = Path(str(artifact.get("path") or "")).resolve()
    if not path.is_file():
        raise HTTPException(status_code=410, detail=f"Artifact expired or missing: {artifact_name}")
    return FileResponse(path=str(path), filename=artifact_name)


def _is_model_downloaded(model_name: str) -> bool:
    """使用统一缓存解析器检查模型主文件是否已下载。"""
    if model_name not in MODEL_CONFIGS:
        return False
    try:
        return resolve_local_model_path(
            str(MODEL_CONFIGS[model_name].get("model") or ""),
            _model_cache_roots(),
        ) is not None
    except Exception:
        return False


def _validate_workflow_with_local_models(config):
    """校验工作流能力与本地模型状态，不触发任何下载。"""
    capabilities = get_model_capabilities()
    for model_name, model_capabilities in capabilities.items():
        model_capabilities["downloaded"] = _is_model_downloaded(model_name)
    errors, warnings = workflow_service.validate_workflow_config(config, capabilities)
    if config.diarization.enabled:
        speaker_path = resolve_local_model_path(
            config.diarization.speaker_model,
            _model_cache_roots(),
        )
        if speaker_path is None:
            errors.append(
                {
                    "code": "MODEL_NOT_DOWNLOADED",
                    "path": "diarization.speaker_model",
                    "message": f"说话人模型 {config.diarization.speaker_model} 尚未下载",
                }
            )
    return errors, warnings


@app.get("/v1/models")
async def list_models():
    """List available models (OpenAI-compatible)."""
    models = []
    for name in MODEL_CONFIGS:
        models.append({
            "id": name,
            "object": "model",
            "created": SERVER_START_EPOCH,
            "owned_by": "funasr",
            "ready": _is_model_ready(name),
            "downloaded": _is_model_downloaded(name),
            "capabilities": MODEL_CAPABILITIES.get(
                name,
                {
                    "offline_asr": False,
                    "streaming_asr": False,
                    "diarization": False,
                    "emotion": False,
                    "vad": False,
                    "punc": False,
                    "notes": "",
                },
            ),
        })
    return JSONResponse({"object": "list", "data": models})


@app.get("/v1/models/{model}/status")
async def model_status(model: str):
    """查询模型是否已加载完成。"""
    try:
        return JSONResponse(_model_load_state(model))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/v1/models/{model}/load")
async def preload_model(model: str):
    """主动加载模型，供前端在开始收音前等待。"""
    if model not in MODEL_CONFIGS:
        raise HTTPException(
            status_code=404,
            detail=f"Model '{model}' not found. Available: {list(MODEL_CONFIGS.keys())}",
        )
    try:
        await run_in_threadpool(load_model, model)
        return JSONResponse(_model_load_state(model))
    except Exception as exc:
        logger.error(f"Model preload error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


from pydantic import BaseModel
from typing import List, Union

class TranslationRequest(BaseModel):
    text: Union[str, List[str]]
    source_lang: str
    target_lang: str
    model: str = "nllb-200-distilled-600m"
    num_beams: int = 5
    max_length: int = 512


class HotwordUpdateRequest(BaseModel):
    hotword: str = ""


@app.get("/v1/funasr/hotwords")
def list_hotwords():
    """查询全部运行时热词覆盖（5e 热词热更新，免重启生效）。"""
    with _HOTWORD_LOCK:
        return {"hotwords": dict(_HOTWORD_OVERRIDES)}


@app.put("/v1/funasr/hotwords/{model}")
def update_hotword(model: str, req: HotwordUpdateRequest):
    """热更新指定模型的热词：空串表示清除覆盖，恢复模型默认行为。"""
    model = model.strip()
    if not model:
        raise HTTPException(status_code=400, detail="model 不能为空")
    value = str(req.hotword or "").strip()
    with _HOTWORD_LOCK:
        if value:
            _HOTWORD_OVERRIDES[model] = value
        else:
            _HOTWORD_OVERRIDES.pop(model, None)
    return {"model": model, "hotword": value}


@app.delete("/v1/funasr/hotwords/{model}")
def clear_hotword(model: str):
    """清除指定模型的运行时热词覆盖。"""
    model = model.strip()
    with _HOTWORD_LOCK:
        removed = _HOTWORD_OVERRIDES.pop(model, None)
    return {"model": model, "removed": removed is not None}


@app.get("/v1/funasr/llm/fuse-state")
def get_llm_fuse_state():
    """返回 LLM 熔断状态只读快照（5b 熔断状态可视化）。

    用 openai_api.llm_client 的同一模块实例，与真实调用链共享状态。
    """
    try:
        from openai_api import llm_client
        snapshot = llm_client.fuse_state_snapshot()
    except Exception as exc:
        logger.error(f"Failed to read fuse state: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"fuse_state": snapshot}


@app.post("/v1/translations")
async def translate_text(req: TranslationRequest):
    """跨语言翻译服务接口。"""
    if req.model not in MODEL_CONFIGS:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{req.model}' not found. Available: {list(MODEL_CONFIGS.keys())}"
        )
    model_cfg = MODEL_CONFIGS[req.model]
    if model_cfg.get("type") != "translation":
        raise HTTPException(
            status_code=400,
            detail=f"Model '{req.model}' is not a translation model."
        )

    # 动态从 translation_languages 导入支持的语言集合
    try:
        from pat_funasr_webui.translation_languages import TRANSLATION_LANGUAGES_MAP
        supported_langs = set(TRANSLATION_LANGUAGES_MAP.keys())
    except ImportError as exc:
        # fallback 机制，万一导入失败则回退到最初的9个语言
        logger.warning(f"Failed to import translation languages, fallback to default 9: {exc}")
        supported_langs = {
            "zho_Hans", "zho_Hant", "eng_Latn", "jpn_Jpan", "kor_Kore",
            "fra_Latn", "tha_Thai", "zsm_Latn", "vie_Latn"
        }

    if req.source_lang not in supported_langs:
        raise HTTPException(
            status_code=400,
            detail=f"source_lang '{req.source_lang}' is not supported. Supported: {list(supported_langs)}"
        )
    if req.target_lang not in supported_langs:
        raise HTTPException(
            status_code=400,
            detail=f"target_lang '{req.target_lang}' is not supported. Supported: {list(supported_langs)}"
        )

    try:
        model_obj = await run_in_threadpool(load_model, req.model)
    except Exception as exc:
        logger.error(f"Failed to load translation model '{req.model}': {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to load model: {exc}")

    try:
        import inspect
        sig = inspect.signature(model_obj.translate)
        kwargs = {}
        if "num_beams" in sig.parameters:
            kwargs["num_beams"] = req.num_beams
        if "max_length" in sig.parameters:
            kwargs["max_length"] = req.max_length

        translated = await run_in_threadpool(
            partial(
                model_obj.translate,
                req.text,
                req.source_lang,
                req.target_lang,
                **kwargs,
            )
        )
        return {"translated_text": translated}
    except Exception as exc:
        logger.error(f"Translation error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/mic-stream")
async def native_mic_stream_page():
    """原生浏览器 Mic 流式识别页，绕过 Gradio Audio 采集层。"""
    return Response(build_native_mic_stream_html(), media_type="text/html; charset=utf-8")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "device": DEVICE,
        "models_loaded": _loaded_model_names(),
        "models_available": list(MODEL_CONFIGS.keys()),
    }


def _resource_status() -> dict:
    """以可选依赖采集 CPU、内存与 GPU 状态；无法采集时明确标记 unavailable。"""
    resources = {
        "cpu": {"available": False},
        "memory": {"available": False},
        "gpu": {"available": False},
    }
    try:
        import psutil

        virtual_memory = psutil.virtual_memory()
        resources["cpu"] = {
            "available": True,
            "percent": float(psutil.cpu_percent(interval=None)),
            "logical_count": psutil.cpu_count(logical=True),
        }
        resources["memory"] = {
            "available": True,
            "total_bytes": int(virtual_memory.total),
            "available_bytes": int(virtual_memory.available),
            "percent": float(virtual_memory.percent),
        }
    except Exception as exc:
        resources["cpu"]["reason"] = str(exc)
        resources["memory"]["reason"] = str(exc)
    try:
        import torch

        if torch.cuda.is_available():
            index = torch.cuda.current_device()
            resources["gpu"] = {
                "available": True,
                "device": torch.cuda.get_device_name(index),
                "allocated_bytes": int(torch.cuda.memory_allocated(index)),
                "reserved_bytes": int(torch.cuda.memory_reserved(index)),
                "total_bytes": int(torch.cuda.get_device_properties(index).total_memory),
            }
        else:
            resources["gpu"]["reason"] = "CUDA unavailable"
    except Exception as exc:
        resources["gpu"]["reason"] = str(exc)
    return resources


@app.get("/v1/runtime/status")
async def runtime_status():
    """返回模型、运行资源和工作流队列的统一状态。"""
    return JSONResponse(
        {
            "status": "ok",
            "device": DEVICE,
            "uptime_seconds": max(0, int(time.time()) - SERVER_START_EPOCH),
            "resources": _resource_status(),
            "models": {
                "loaded": _loaded_model_names(),
                "loading": sorted(
                    name
                    for name, state in MODEL_LOAD_STATUS.items()
                    if state.get("state") == "loading"
                ),
                "available": len(MODEL_CONFIGS),
            },
            "workflow_queue": WORKFLOW_MANAGER.queue_summary(),
        }
    )


def main():
    parser = argparse.ArgumentParser(description="FunASR OpenAI-Compatible API Server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8000, help="Bind port")
    parser.add_argument("--device", default="cuda", help="Device: cuda, cpu, mps")
    parser.add_argument("--model", default="sensevoice", help="(deprecated, unused) Model alias for lazy loading")
    args = parser.parse_args()

    global DEVICE
    DEVICE = args.device

    if _MODEL_IDLE_TTL_S > 0:
        _start_model_idle_reaper()
        logger.info(f"  Model idle release: TTL={_MODEL_IDLE_TTL_S}s sweep={_MODEL_IDLE_SWEEP_S}s")

    logger.info(f"FunASR API server starting on http://{args.host}:{args.port}")
    logger.info(f"  Device: {DEVICE}")
    logger.info(f"  Models: {list(MODEL_CONFIGS.keys())}")
    logger.info(f"  Docs:   http://{args.host}:{args.port}/docs")
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="warning",  # 只输出 WARNING/ERROR，屏蔽 INFO 级别的 access log 和轮询请求
        access_log=False,     # 关闭 access log（/events 轮询刷屏主要来源）
    )


if __name__ == "__main__":
    main()
