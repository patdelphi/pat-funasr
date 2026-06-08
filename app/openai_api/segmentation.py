"""
程序说明：
提供转写结果的分段与时长探测工具：
- 使用 ffprobe 获取音视频时长（秒）
- 在缺少 sentence_info 时，将文本按标点/长度切分为多段，并均匀分配时间戳
"""

from __future__ import annotations

import re
import subprocess
from typing import Any, Callable, Dict, List, Optional


def ffprobe_duration_s(path: str) -> float:
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            stderr=subprocess.STDOUT,
        )
        return float(out.decode("utf-8", errors="replace").strip() or "0")
    except Exception:
        return 0.0


def _split_text(text: str) -> List[str]:
    t = (text or "").strip()
    if not t:
        return []

    parts = re.split(r"(?<=[。！？!?；;])\s*", t)
    parts = [p.strip() for p in parts if p and p.strip()]
    if len(parts) >= 2:
        return parts

    chunk_size = 30
    chunks: List[str] = []
    buf: List[str] = []
    for ch in t:
        buf.append(ch)
        if len(buf) >= chunk_size:
            chunks.append("".join(buf).strip())
            buf = []
    if buf:
        chunks.append("".join(buf).strip())
    return [c for c in chunks if c]


def build_segments_from_sentence_info(
    sentence_info: Any,
    *,
    clean_text: Callable[[str], str],
) -> List[Dict[str, Any]]:
    if not isinstance(sentence_info, list):
        return []

    segments: List[Dict[str, Any]] = []
    for seg in sentence_info:
        if not isinstance(seg, dict):
            continue
        segments.append(
            {
                "start": (seg.get("start", 0) or 0) / 1000.0,
                "end": (seg.get("end", 0) or 0) / 1000.0,
                "text": clean_text(str(seg.get("text", "") or "")),
                "speaker": seg.get("spk", None),
            }
        )
    return [s for s in segments if s.get("text")]


def build_segments_from_text(
    text: str,
    *,
    duration_s: float,
    clean_text: Callable[[str], str],
    max_segments: int = 200,
) -> List[Dict[str, Any]]:
    t = clean_text(text or "")
    parts = _split_text(t)
    if not parts:
        return []

    n = min(len(parts), max_segments)
    parts = parts[:n]

    dur = float(duration_s or 0.0)
    if dur <= 0:
        dur = max(0.001, n * 0.2)

    step = dur / float(n)
    segments: List[Dict[str, Any]] = []
    for i, p in enumerate(parts):
        start = step * i
        end = dur if i == n - 1 else (step * (i + 1))
        segments.append({"start": round(start, 3), "end": round(end, 3), "text": p, "speaker": None})
    return segments


def build_segments(
    *,
    result0: Dict[str, Any],
    duration_s: float,
    clean_text: Callable[[str], str],
) -> List[Dict[str, Any]]:
    segments = build_segments_from_sentence_info(result0.get("sentence_info"), clean_text=clean_text)
    if segments:
        return segments
    return build_segments_from_text(result0.get("text", ""), duration_s=duration_s, clean_text=clean_text)

