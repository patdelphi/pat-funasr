"""
FunASR OpenAI-Compatible API Server

Drop-in replacement for OpenAI's /v1/audio/transcriptions endpoint.
Works with any agent framework that supports OpenAI audio API.

Usage:
    python server.py --model sensevoice --device cuda --port 8000

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
from typing import Optional
import uuid

import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, Response

import renderers
import vad_presets
import segmentation

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="FunASR OpenAI-Compatible API", version="1.0.0")

MODEL_REGISTRY = {}
DEVICE = "cpu"

MODEL_CONFIGS = {
    "sensevoice": {
        "model": "iic/SenseVoiceSmall",
        "hub": "ms",
        "vad_model": "fsmn-vad",
        "vad_kwargs": {"max_single_segment_time": 30000},
        "punc_model": "ct-punc",
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

    if hub:
        cfg["hub"] = hub
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
    """Load a model and store in registry."""
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
    if registry_key in MODEL_REGISTRY:
        return MODEL_REGISTRY[registry_key]

    from funasr import AutoModel

    # Try to find local model cache first
    model_id = cfg["model"]
    if "/" in model_id and "hub" not in cfg:
        # ModelScope local cache lookup
        cache_root = os.environ.get("MODELSCOPE_CACHE", "")
        if cache_root:
            # ModelScope creates models/<org>/<repo> under cache dir
            local_paths = [
                os.path.join(cache_root, model_id, "model.pt"),
                os.path.join(cache_root, "models", model_id, "model.pt"),
            ]
            for pt in local_paths:
                if os.path.exists(pt):
                    # Pass local dir as model; keep original model_id for vad lookup
                    cfg["model"] = os.path.dirname(pt)
                    logger.info(f"Using local model: {cfg['model']}")
                    break

    logger.info(f"Loading model '{model_name}' on {cfg['device']}...")
    t0 = time.time()
    model = AutoModel(**cfg)
    elapsed = time.time() - t0
    logger.info(f"Model '{model_name}' loaded in {elapsed:.1f}s")

    MODEL_REGISTRY[registry_key] = model
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

    # Save uploaded file
    suffix = os.path.splitext(file.filename)[1] if file.filename else ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

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
                vad_max_single_segment_time=vad_max_single_segment_time,
            )
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))

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
        text = clean_text(result[0].get("text", ""))
        segments = segmentation.build_segments(result0=result[0], duration_s=duration_s, clean_text=clean_text)
        if not segments:
            segments = [{"start": 0.0, "end": round(duration_s, 3), "text": text, "speaker": None}]

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
            content = renderers.render_txt(segments, max_line_width=max_line_width)
            return Response(content=content, media_type="text/plain; charset=utf-8")
        if response_format == "tsv":
            content = renderers.render_tsv(segments)
            return Response(content=content, media_type="text/tab-separated-values; charset=utf-8")
        if response_format == "srt":
            content = renderers.render_srt(segments, max_line_width=max_line_width)
            return Response(content=content, media_type="application/x-subrip; charset=utf-8")
        if response_format == "vtt":
            content = renderers.render_vtt(segments, max_line_width=max_line_width)
            return Response(content=content, media_type="text/vtt; charset=utf-8")
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
    finally:
        os.unlink(tmp_path)


@app.post("/v1/funasr/streaming")
async def transcribe_streaming(
    file: UploadFile = File(...),
    model: str = Form(default="paraformer-zh-streaming"),
    session_id: Optional[str] = Form(default=None),
    reset: Optional[bool] = Form(default=False),
    is_final: Optional[bool] = Form(default=False),
    chunk_size: str = Form(default="0,10,5"),
    encoder_chunk_look_back: int = Form(default=0),
    decoder_chunk_look_back: int = Form(default=0),
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
        result = asr_model.generate(
            input=chunk,
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
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

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
    finally:
        os.unlink(tmp_path)


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
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

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
    finally:
        os.unlink(tmp_path)


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


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "device": DEVICE,
        "models_loaded": _loaded_model_names(),
        "model_variants_loaded": sorted(MODEL_REGISTRY.keys()),
        "models_available": list(MODEL_CONFIGS.keys()),
    }


def main():
    parser = argparse.ArgumentParser(description="FunASR OpenAI-Compatible API Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8000, help="Bind port")
    parser.add_argument("--device", default="cuda", help="Device: cuda, cpu, mps")
    parser.add_argument("--model", default="sensevoice", help="Default model alias for lazy loading")
    args = parser.parse_args()

    global DEVICE
    DEVICE = args.device

    logger.info(f"FunASR API server starting on http://{args.host}:{args.port}")
    logger.info(f"  Device: {DEVICE}")
    logger.info(f"  Default model alias: {args.model} (lazy-load)")
    logger.info(f"  Models: {list(MODEL_CONFIGS.keys())}")
    logger.info(f"  Docs:   http://{args.host}:{args.port}/docs")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
