"""
程序说明：
批量转写执行器（供 "run_test_all_models.ps1" 调用）。

功能：
- 使用 FunASR AutoModel 对输入音视频进行转写
- 生成 txt/tsv/srt/vtt/json/zip 多格式输出
- 优先使用 sentence_info 生成段级时间戳；缺失时用文本切分进行兜底分段
- 将运行参数与异常堆栈写入 log 文件，便于排查
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


def clean_text(text: str) -> str:
    text = re.sub(r"<\|[^|]*\|>", "", text or "")
    text = re.sub(r"<[^<>]*>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def ensure_wav_16k_mono(src: Path, dst_wav: Path, log_path: Path) -> Path:
    if src.suffix.lower() == ".wav" and dst_wav.exists():
        return dst_wav
    if src.suffix.lower() == ".wav":
        return src

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        str(dst_wav),
    ]
    log_line(log_path, f"[{now()}] ffmpeg: {cmd!r}")
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dst_wav


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
    "fun-asr-nano": {
        "model": "FunAudioLLM/Fun-ASR-Nano-2512",
        "hub": "ms",
        "trust_remote_code": True,
        "vad_model": "fsmn-vad",
        "vad_kwargs": {"max_single_segment_time": 30000},
    },
    "qwen3-asr": {
        "model": "Qwen/Qwen3-ASR-1.7B",
        "hub": "ms",
        "trust_remote_code": True,
        "dtype": "fp16",
        "vad_model": "fsmn-vad",
        "vad_kwargs": {"max_single_segment_time": 30000},
    },
}

_TRY_SENTENCE_TIMESTAMP = {"paraformer", "fun-asr-nano"}


def _load_module_from_path(name: str, path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块：{path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-alias", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--log-path", required=True)
    parser.add_argument("inputs", nargs="+")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = Path(args.log_path)

    repo = Path(__file__).resolve().parents[1]
    os.environ.setdefault("MODELSCOPE_CACHE", str(repo / "workspace" / "models"))
    os.environ.setdefault("HF_HOME", str(repo / "workspace" / "models" / "huggingface"))
    segmentation = _load_module_from_path("segmentation_mod", repo / "app" / "openai_api" / "segmentation.py")
    renderers = _load_module_from_path("renderers_mod", repo / "app" / "openai_api" / "renderers.py")

    def excepthook(exc_type, exc, tb):
        log_line(log_path, f"[{now()}] exception:")
        for line in traceback.format_exception(exc_type, exc, tb):
            for ln in line.rstrip("\n").splitlines():
                log_line(log_path, ln)

    sys.excepthook = excepthook

    try:
        log_line(log_path, f"[{now()}] model_alias={args.model_alias}")
        log_line(log_path, f"[{now()}] cwd={os.getcwd()}")
        log_line(log_path, f"[{now()}] MODELSCOPE_CACHE={os.environ.get('MODELSCOPE_CACHE','')}")
        log_line(log_path, f"[{now()}] HF_HOME={os.environ.get('HF_HOME','')}")

        if args.model_alias not in MODEL_CONFIGS:
            raise SystemExit(f"Unknown model_alias: {args.model_alias}, allowed={list(MODEL_CONFIGS.keys())}")

        from funasr import AutoModel
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        log_line(log_path, f"[{now()}] device={device}")

        cfg = dict(MODEL_CONFIGS[args.model_alias])
        cfg["device"] = device
        cfg["disable_update"] = True
        log_line(log_path, f"[{now()}] AutoModel(**cfg)={json.dumps(cfg, ensure_ascii=False)}")

        model = AutoModel(**cfg)

        for in_path_str in args.inputs:
            src = Path(in_path_str)
            if not src.exists():
                log_line(log_path, f"[{now()}] skip (missing): {src}")
                continue

            log_line(log_path, f"[{now()}] input={str(src)}")

            wav_cache = out_dir / f"{src.stem}.wav"
            wav = ensure_wav_16k_mono(src, wav_cache, log_path)
            dur = segmentation.ffprobe_duration_s(str(wav))

            generate_kwargs = {"input": str(wav), "batch_size": 1}
            if args.model_alias in _TRY_SENTENCE_TIMESTAMP:
                generate_kwargs["sentence_timestamp"] = True
            log_line(log_path, f"[{now()}] generate(**kwargs)={json.dumps(generate_kwargs, ensure_ascii=False)}")

            try:
                res = model.generate(**generate_kwargs)
            except KeyError as ke:
                if str(ke) == "'timestamp'" and "sentence_timestamp" in generate_kwargs:
                    generate_kwargs.pop("sentence_timestamp", None)
                    log_line(log_path, f"[{now()}] retry_generate(remove_sentence_timestamp)=true")
                    log_line(
                        log_path,
                        f"[{now()}] generate(**kwargs)={json.dumps(generate_kwargs, ensure_ascii=False)}",
                    )
                    res = model.generate(**generate_kwargs)
                else:
                    raise
            text = clean_text(res[0].get("text", ""))
            segments = segmentation.build_segments(result0=res[0], duration_s=float(dur or 0.0), clean_text=clean_text)
            if not segments:
                segments = [{"start": 0.0, "end": float(dur or 0.0), "text": text, "speaker": None}]

            payload = renderers.build_verbose_json_payload(
                full_text=text,
                segments=segments,
                meta={
                    "language": "auto",
                    "duration": round(float(dur or 0.0), 3),
                    "model": args.model_alias,
                    "device": device,
                },
            )

            base = out_dir / f"{src.stem}"
            base.with_suffix(".txt").write_text(renderers.render_txt(segments), encoding="utf-8")
            base.with_suffix(".tsv").write_text(renderers.render_tsv(segments), encoding="utf-8")
            base.with_suffix(".srt").write_text(renderers.render_srt(segments), encoding="utf-8")
            base.with_suffix(".vtt").write_text(renderers.render_vtt(segments), encoding="utf-8")
            base.with_suffix(".json").write_text(renderers.render_json_pretty(payload), encoding="utf-8")
            base.with_suffix(".zip").write_bytes(
                renderers.render_all_zip(full_text=text, segments=segments, json_payload=payload)
            )

            log_line(log_path, f"[{now()}] wrote={base.name}.[txt|tsv|srt|vtt|json|zip]")

        log_line(log_path, f"[{now()}] done")
        return 0
    except SystemExit:
        raise
    except Exception:
        log_line(log_path, f"[{now()}] exception:")
        for line in traceback.format_exception(*sys.exc_info()):
            for ln in line.rstrip("\n").splitlines():
                log_line(log_path, ln)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
