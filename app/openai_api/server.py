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

import argparse
import tempfile
import time
import os
import re
import logging
import json
from typing import Optional
import threading
import uuid
import urllib.request

import numpy as np
import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, Response

import renderers
import vad_presets
import segmentation

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# #region debug-point C:debug-report
def is_debug_report_enabled() -> bool:
    """判断是否启用本地调试事件上报；默认关闭，避免常规运行时访问调试端口。"""
    return str(os.environ.get("FUNASR_DEBUG_REPORT", "")).strip().lower() in {"1", "true", "yes", "on"}


def _dbg_report(
    *,
    hypothesis_id: str,
    msg: str,
    location: str,
    data: dict | None = None,
    trace_id: str | None = None,
    run_id: str = "pre-fix",
) -> None:
    """后台线程发送调试事件，避免阻塞 asyncio 事件循环。"""
    if not is_debug_report_enabled():
        return

    def _send() -> None:
        try:
            root = Path(__file__).resolve().parents[2]
            env_path = root / ".dbg" / "gradio-page-hung.env"
            url = "http://127.0.0.1:7777/event"
            session_id = "gradio-page-hung"
            try:
                content = env_path.read_text(encoding="utf-8", errors="replace")
                for line in content.splitlines():
                    if line.startswith("DEBUG_SERVER_URL="):
                        url = line.split("=", 1)[1].strip() or url
                    elif line.startswith("DEBUG_SESSION_ID="):
                        session_id = line.split("=", 1)[1].strip() or session_id
            except Exception:
                pass
            payload = {
                "sessionId": session_id,
                "runId": run_id,
                "hypothesisId": hypothesis_id,
                "location": location,
                "msg": f"[DEBUG] {msg}",
                "data": data or {},
                "traceId": trace_id,
                "ts": int(time.time() * 1000),
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=2).read()
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True).start()


# #endregion

app = FastAPI(title="FunASR OpenAI-Compatible API", version="1.0.0")

MODEL_REGISTRY = {}
MODEL_LOAD_STATUS: dict[str, dict] = {}
MODEL_LOAD_LOCK = threading.Lock()
DEVICE = "cpu"

MODEL_CONFIGS = {
    "sensevoice": {
        "model": "iic/SenseVoiceSmall",
        "hub": "ms",
        "vad_model": "fsmn-vad",
        "vad_kwargs": {"max_single_segment_time": 30000},
    },
    "paraformer": {
        "model": "paraformer-zh",
        "hub": "ms",
        "vad_model": "fsmn-vad",
        "punc_model": "ct-punc",
    },
    "paraformer-en": {
        "model": "paraformer-en",
        "hub": "ms",
        "vad_model": "fsmn-vad",
    },
    "paraformer-zh-streaming": {
        "model": "paraformer-zh-streaming",
        "hub": "ms",
        "punc_model": "ct-punc",
    },
    "fun-asr-nano": {
        "model": "FunAudioLLM/Fun-ASR-Nano-2512",
        "hub": "ms",
        "trust_remote_code": False,
        "vad_model": "fsmn-vad",
        "vad_kwargs": {"max_single_segment_time": 30000},
    },
    "qwen3-asr": {
        "model": "Qwen/Qwen3-ASR-1.7B",
        "hub": "ms",
        "trust_remote_code": False,
        "dtype": "fp16",
        "vad_model": "fsmn-vad",
        "vad_kwargs": {"max_single_segment_time": 30000},
    },
    "qwen3-asr-0.6b": {
        "model": "Qwen/Qwen3-ASR-0.6B",
        "hub": "ms",
        "trust_remote_code": False,
        "dtype": "fp16",
        "vad_model": "fsmn-vad",
        "vad_kwargs": {"max_single_segment_time": 30000},
    },
    "emotion2vec-plus-large": {
        "model": "iic/emotion2vec_plus_large",
        "hub": "ms",
    },
}

MODEL_CAPABILITIES = {
    "sensevoice": {
        "offline_asr": True,
        "streaming_asr": False,
        "diarization": True,
        "emotion": True,
        "vad": True,
        "punc": True,
        "notes": "多语言；支持说话人分离，也可直接输出情感标签",
    },
    "paraformer": {
        "offline_asr": True,
        "streaming_asr": False,
        "diarization": True,
        "emotion": False,
        "vad": True,
        "punc": True,
        "notes": "中文离线识别；支持 cam++ 说话人分离",
    },
    "paraformer-en": {
        "offline_asr": True,
        "streaming_asr": False,
        "diarization": False,
        "emotion": False,
        "vad": True,
        "punc": False,
        "notes": "英文离线识别",
    },
    "paraformer-zh-streaming": {
        "offline_asr": False,
        "streaming_asr": True,
        "diarization": False,
        "emotion": False,
        "vad": False,
        "punc": True,
        "notes": "流式识别专用；默认挂载 ct-punc 提升断句与可读性",
    },
    "fun-asr-nano": {
        "offline_asr": True,
        "streaming_asr": False,
        "diarization": True,
        "emotion": False,
        "vad": True,
        "punc": True,
        "notes": "轻量多语言模型；支持 cam++ 说话人分离",
    },
    "qwen3-asr": {
        "offline_asr": True,
        "streaming_asr": False,
        "diarization": False,
        "emotion": False,
        "vad": True,
        "punc": True,
        "notes": "高精度离线识别",
    },
    "qwen3-asr-0.6b": {
        "offline_asr": True,
        "streaming_asr": False,
        "diarization": False,
        "emotion": False,
        "vad": True,
        "punc": True,
        "notes": "轻量版 Qwen3-ASR",
    },
    "emotion2vec-plus-large": {
        "offline_asr": False,
        "streaming_asr": False,
        "diarization": False,
        "emotion": True,
        "vad": False,
        "punc": False,
        "notes": "独立情感识别模型",
    },
}

STREAMING_MODELS = {"paraformer-zh-streaming"}
EMOTION_MODELS = {"emotion2vec-plus-large", "sensevoice"}
DIARIZATION_MODELS = {"paraformer", "fun-asr-nano", "sensevoice"}
STREAMING_SESSIONS: dict[str, dict] = {}
STREAMING_SESSION_TTL_S = 3600
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
VALID_PUNC_MODES = {"auto", "disabled"}
VALID_MODEL_HUBS = {"ms", "modelscope", "hf", "huggingface"}


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
        STREAMING_SESSIONS[session_id] = {"model": model, "cache": {}, "full_text": "", "updated_at": now}
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
    ]
    return f"{model_name}::{'|'.join(variant_parts)}"


def _is_model_ready(model_name: str) -> bool:
    """只按模型主名判断是否已有至少一个变体加载完成。"""
    return any(key == model_name or key.startswith(f"{model_name}::") for key in MODEL_REGISTRY)


def _loaded_model_names() -> list[str]:
    """返回已加载的模型主名列表，避免把内部变体 key 暴露到用户界面。"""
    return sorted({str(key).split("::", 1)[0] for key in MODEL_REGISTRY})


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
    )
    registry_key = build_model_registry_key(model_name, cfg)

    with MODEL_LOAD_LOCK:
        if registry_key in MODEL_REGISTRY:
            MODEL_LOAD_STATUS[model_name] = {"state": "ready", "error": None, "updated_at": time.time()}
            return MODEL_REGISTRY[registry_key]

    from funasr import AutoModel

    # Try to find local model cache first
    model_id = cfg["model"]
    if "/" in model_id and "hub" not in cfg:
        # ModelScope local cache lookup
        cache_root = os.environ.get("MODELSCOPE_CACHE", "")
        if cache_root:
            local_paths = [
                os.path.join(cache_root, model_id, "model.pt"),
                os.path.join(cache_root, "models", model_id, "model.pt"),
            ]
            for pt in local_paths:
                if os.path.exists(pt):
                    cfg["model"] = os.path.dirname(pt)
                    logger.info(f"Using local model: {cfg['model']}")
                    break

    logger.info(f"Loading model '{model_name}' on {cfg['device']}...")
    MODEL_LOAD_STATUS[model_name] = {"state": "loading", "error": None, "updated_at": time.time()}
    t0 = time.time()
    try:
        model = AutoModel(**cfg)
    except Exception as exc:
        MODEL_LOAD_STATUS[model_name] = {"state": "error", "error": str(exc), "updated_at": time.time()}
        raise
    elapsed = time.time() - t0
    logger.info(f"Model '{model_name}' loaded in {elapsed:.1f}s")

    with MODEL_LOAD_LOCK:
        MODEL_REGISTRY[registry_key] = model
        MODEL_LOAD_STATUS[model_name] = {"state": "ready", "error": None, "updated_at": time.time()}
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

    max_overlap = min(len(previous), len(current))
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
):
    """构建传给 FunASR generate() 的白名单参数。"""
    generate_kwargs = {"input": tmp_path, "batch_size": 1}
    if language:
        generate_kwargs["language"] = language
    if hotword:
        generate_kwargs["hotword"] = hotword
    if use_itn is not None:
        generate_kwargs["use_itn"] = use_itn
    if batch_size_s is not None:
        if int(batch_size_s) <= 0:
            raise ValueError("batch_size_s must be > 0")
        generate_kwargs["batch_size_s"] = int(batch_size_s)
    if batch_size_threshold_s is not None:
        if int(batch_size_threshold_s) <= 0:
            raise ValueError("batch_size_threshold_s must be > 0")
        generate_kwargs["batch_size_threshold_s"] = int(batch_size_threshold_s)
    if model in {"paraformer", "fun-asr-nano"}:
        generate_kwargs["sentence_timestamp"] = True
    return vad_presets.apply_vad_controls(
        generate_kwargs=generate_kwargs,
        vad_preset=vad_preset,
        merge_vad=merge_vad,
        merge_length_s=merge_length_s,
        vad_max_single_segment_time=vad_max_single_segment_time,
    )


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

    allowed_formats = {"json", "verbose_json", "txt", "srt", "vtt", "tsv", "all"}
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

    # Save uploaded file (with size limit)
    trace_id = uuid.uuid4().hex
    MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
    suffix = os.path.splitext(file.filename)[1] if file.filename else ".wav"
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Upload too large: {len(content)} bytes exceeds limit of {MAX_UPLOAD_BYTES} bytes",
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = os.path.join(tmpdir, f"upload{suffix}")
        with open(tmp_path, "wb") as f:
            f.write(content)

        _dbg_report(
            hypothesis_id="D",
            msg="api_upload_saved",
            location="openai_api/server.py:/v1/audio/transcriptions",
            trace_id=trace_id,
            data={
                "model": model,
                "response_format": response_format,
                "filename": getattr(file, "filename", ""),
                "upload_bytes": len(content) if content is not None else 0,
                "tmp_bytes": os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0,
            },
        )

        try:
            try:
                asr_model = load_model(
                    model,
                    device=device,
                    hub=hub,
                    disable_update=disable_update,
                    ncpu=ncpu,
                    log_level=log_level,
                    disable_pbar=disable_pbar,
                    punc_mode=punc_mode,
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
                    hotword=hotword,
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
            _dbg_report(
                hypothesis_id="D",
                msg="api_generate_start",
                location="openai_api/server.py:/v1/audio/transcriptions",
                trace_id=trace_id,
                data={
                    "duration_s": duration_s,
                    "generate_kwargs_keys": sorted(list(generate_kwargs.keys())),
                },
            )
            try:
                result = asr_model.generate(**generate_kwargs)
            except KeyError as ke:
                if str(ke) == "'timestamp'" and "sentence_timestamp" in generate_kwargs:
                    generate_kwargs.pop("sentence_timestamp", None)
                    result = asr_model.generate(**generate_kwargs)
                else:
                    raise
            elapsed = time.time() - t0

            duration_s = duration_s if duration_s > 0 else elapsed
            try:
                rtf = elapsed / duration_s if duration_s > 0 else 0.0
            except Exception:
                rtf = 0.0
            logger.info(
                "Transcription done: "
                f"model={model}, "
                f"elapsed_s={elapsed:.2f}, "
                f"duration_s={duration_s:.2f}, "
                f"rtf={rtf:.3f}"
            )
            result0 = result[0] if result else {"text": ""}
            text = clean_text(result0.get("text", ""))
            segments = segmentation.build_segments(result0=result0, duration_s=duration_s, clean_text=clean_text)
            if not segments:
                segments = [{"start": 0.0, "end": round(duration_s, 3), "text": text, "speaker": None}]
            _dbg_report(
                hypothesis_id="D",
                msg="api_generate_done",
                location="openai_api/server.py:/v1/audio/transcriptions",
                trace_id=trace_id,
                data={
                    "elapsed_s": round(elapsed, 3),
                    "duration_s": round(duration_s, 3),
                    "rtf": round(float(rtf), 4),
                    "text_len": len(text or ""),
                    "segments": len(segments) if isinstance(segments, list) else 0,
                },
            )

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
            if response_format == "all":
                zbytes = renderers.render_all_zip(
                    full_text=text,
                    segments=segments,
                    json_payload=verbose_payload,
                    max_line_width=max_line_width,
                )
                headers = {"Content-Disposition": "attachment; filename=\"output.zip\""}
                return Response(content=zbytes, media_type="application/zip", headers=headers)

            return JSONResponse({"text": text})

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
        chunk = await file.read()
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
        asr_model = load_model(model)
        speech_chunk = pcm16_bytes_to_float32_audio(chunk)
        result = asr_model.generate(
            input=speech_chunk,
            cache=state["cache"],
            is_final=bool(is_final),
            chunk_size=parsed_chunk_size,
            encoder_chunk_look_back=int(encoder_chunk_look_back),
            decoder_chunk_look_back=int(decoder_chunk_look_back),
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

    suffix = os.path.splitext(file.filename)[1] if file.filename else ".wav"
    content = await file.read()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = os.path.join(tmpdir, f"upload{suffix}")
        with open(tmp_path, "wb") as f:
            f.write(content)

        try:
            emotion_model = load_model(model)
            result = emotion_model.generate(
                input=tmp_path,
                granularity=granularity,
                extract_embedding=False,
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

    suffix = os.path.splitext(file.filename)[1] if file.filename else ".wav"
    content = await file.read()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = os.path.join(tmpdir, f"upload{suffix}")
        with open(tmp_path, "wb") as f:
            f.write(content)

        try:
            duration_s = segmentation.ffprobe_duration_s(tmp_path)
            effective_spk_mode = resolve_diarization_spk_mode(model, spk_mode)
            load_kwargs = {"spk_model": spk_model}
            # SenseVoice 说话人分离在 vad_segment 下仍加载 punc_model 时，可能触发时间戳异常。
            if model == "sensevoice" and effective_spk_mode == "vad_segment":
                load_kwargs["punc_mode"] = "disabled"
            asr_model = load_model(model, **load_kwargs)
            generate_kwargs = {
                "input": tmp_path,
                "batch_size": 1,
                "spk_mode": effective_spk_mode,
                "return_spk_res": True,
                "output_timestamp": True,
            }
            if preset_spk_num is not None:
                generate_kwargs["preset_spk_num"] = int(preset_spk_num)
            result = asr_model.generate(**generate_kwargs)
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


@app.get("/v1/models")
async def list_models():
    """List available models (OpenAI-compatible)."""
    models = []
    for name in MODEL_CONFIGS:
        models.append({
            "id": name,
            "object": "model",
            "created": 1700000000,
            "owned_by": "funasr",
            "ready": _is_model_ready(name),
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
        load_model(model)
        return JSONResponse(_model_load_state(model))
    except Exception as exc:
        logger.error(f"Model preload error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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


def main():
    parser = argparse.ArgumentParser(description="FunASR OpenAI-Compatible API Server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8000, help="Bind port")
    parser.add_argument("--device", default="cuda", help="Device: cuda, cpu, mps")
    parser.add_argument("--model", default="sensevoice", help="(deprecated, unused) Model alias for lazy loading")
    args = parser.parse_args()

    global DEVICE
    DEVICE = args.device

    logger.info(f"FunASR API server starting on http://{args.host}:{args.port}")
    logger.info(f"  Device: {DEVICE}")
    logger.info(f"  Models: {list(MODEL_CONFIGS.keys())}")
    logger.info(f"  Docs:   http://{args.host}:{args.port}/docs")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
