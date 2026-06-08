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

import argparse
import tempfile
import time
import os
import re
import logging
from typing import Optional

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
        "vad_model": "fsmn-vad",
        "vad_kwargs": {"max_single_segment_time": 30000},
        "punc_model": "ct-punc",
    },
    "paraformer": {
        "model": "paraformer-zh",
        "vad_model": "fsmn-vad",
        "punc_model": "ct-punc",
    },
    "paraformer-en": {
        "model": "paraformer-en",
        "vad_model": "fsmn-vad",
    },
    "fun-asr-nano": {
        "model": "FunAudioLLM/Fun-ASR-Nano-2512",
        "hub": "hf",
        "trust_remote_code": True,
        "vad_model": "fsmn-vad",
        "vad_kwargs": {"max_single_segment_time": 30000},
    },
    "qwen3-asr": {
        "model": "Qwen/Qwen3-ASR-1.7B",
        "hub": "hf",
        "trust_remote_code": True,
        "vad_model": "fsmn-vad",
        "vad_kwargs": {"max_single_segment_time": 30000},
    },
    "qwen3-asr-0.6b": {
        "model": "Qwen/Qwen3-ASR-0.6B",
        "hub": "hf",
        "trust_remote_code": True,
        "vad_model": "fsmn-vad",
        "vad_kwargs": {"max_single_segment_time": 30000},
    },
}


def load_model(model_name: str):
    """Load a model and store in registry."""
    if model_name in MODEL_REGISTRY:
        return MODEL_REGISTRY[model_name]

    if model_name not in MODEL_CONFIGS:
        available = list(MODEL_CONFIGS.keys())
        raise ValueError(f"Unknown model '{model_name}'. Available: {available}")

    from funasr import AutoModel

    cfg = MODEL_CONFIGS[model_name].copy()
    cfg["device"] = DEVICE
    cfg["disable_update"] = False

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

    logger.info(f"Loading model '{model_name}' on {DEVICE}...")
    t0 = time.time()
    model = AutoModel(**cfg)
    elapsed = time.time() - t0
    logger.info(f"Model '{model_name}' loaded in {elapsed:.1f}s")

    MODEL_REGISTRY[model_name] = model
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


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(default="sensevoice"),
    language: Optional[str] = Form(default=None),
    response_format: Optional[str] = Form(default="json"),
    max_line_width: Optional[int] = Form(default=None),
    vad_preset: Optional[str] = Form(default=None),
    merge_vad: Optional[bool] = Form(default=None),
    merge_length_s: Optional[int] = Form(default=None),
):
    """
    OpenAI-compatible audio transcription endpoint.
    
    Accepts the same parameters as OpenAI's /v1/audio/transcriptions:
    - file: Audio file (wav, mp3, flac, m4a, ogg, webm)
    - model: Model to use (sensevoice, paraformer, fun-asr-nano)
    - language: Optional language hint
    - response_format: json/verbose_json/txt/srt/vtt/tsv/all
    - vad_preset: default/anti_hallucination（可选）
    - merge_vad: true/false（可选，优先级高于 preset）
    - merge_length_s: 合并段长度（秒，可选，需要配合 merge_vad=true）
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

    # Save uploaded file
    suffix = os.path.splitext(file.filename)[1] if file.filename else ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        asr_model = load_model(model)
        t0 = time.time()

        duration_s = segmentation.ffprobe_duration_s(tmp_path)
        generate_kwargs = {"input": tmp_path, "batch_size": 1}
        if language:
            generate_kwargs["language"] = language
        if model in {"paraformer", "fun-asr-nano"}:
            generate_kwargs["sentence_timestamp"] = True
        try:
            generate_kwargs = vad_presets.apply_vad_controls(
                generate_kwargs=generate_kwargs,
                vad_preset=vad_preset,
                merge_vad=merge_vad,
                merge_length_s=merge_length_s,
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
            "ready": name in MODEL_REGISTRY,
        })
    return JSONResponse({"object": "list", "data": models})


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "device": DEVICE,
        "models_loaded": list(MODEL_REGISTRY.keys()),
        "models_available": list(MODEL_CONFIGS.keys()),
    }


def main():
    parser = argparse.ArgumentParser(description="FunASR OpenAI-Compatible API Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8000, help="Bind port")
    parser.add_argument("--device", default="cuda", help="Device: cuda, cpu, mps")
    parser.add_argument("--model", default="sensevoice", help="Pre-load model at startup")
    args = parser.parse_args()

    global DEVICE
    DEVICE = args.device

    # Pre-load default model
    load_model(args.model)

    logger.info(f"FunASR API server starting on http://{args.host}:{args.port}")
    logger.info(f"  Device: {DEVICE}")
    logger.info(f"  Models: {list(MODEL_CONFIGS.keys())}")
    logger.info(f"  Docs:   http://{args.host}:{args.port}/docs")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
