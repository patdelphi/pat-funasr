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


_FALLBACK_CHUNK_SIZE = 30


def _split_text(text: str) -> List[str]:
    t = (text or "").strip()
    if not t:
        return []

    parts = re.split(r"(?<=[。！？!?；;])\s*", t)
    parts = [p.strip() for p in parts if p and p.strip()]
    if len(parts) >= 2:
        return parts

    chunk_size = _FALLBACK_CHUNK_SIZE
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


def _to_seconds(value: Any) -> Optional[float]:
    """把 FunASR 返回的毫秒时间戳安全转换为秒。"""
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        return None
    return numeric / 1000.0


def _extract_seconds_from_timestamp_items(items: Any) -> tuple[Optional[float], Optional[float]]:
    """兼容 [[start_ms, end_ms]] 与 [{start_time,end_time}] 两种时间戳格式。"""
    if not isinstance(items, list) or not items:
        return None, None
    first = items[0]
    last = items[-1]
    if isinstance(first, dict) and isinstance(last, dict):
        return _to_seconds(first.get("start_time")), _to_seconds(last.get("end_time"))
    if (
        isinstance(first, (list, tuple))
        and len(first) >= 2
        and isinstance(last, (list, tuple))
        and len(last) >= 2
    ):
        return _to_seconds(first[0]), _to_seconds(last[1])
    return None, None


def _build_segments_from_word_timestamps(
    timestamps: List[Any],
    full_text: str,
) -> List[Dict[str, Any]]:
    """用词级时间戳精确对齐分句。timestamps 为 [[start_ms, end_ms], ...]。"""
    # 转为秒
    words: List[Dict[str, Any]] = []
    for ts in timestamps:
        if isinstance(ts, (list, tuple)) and len(ts) >= 2:
            s = _to_seconds(ts[0])
            e = _to_seconds(ts[1])
            if s is not None and e is not None:
                words.append({"start": s, "end": e})
    if len(words) < 2:
        return []

    # 按标点切句
    parts = re.split(r"(?<=[。！？!?；;])\s*", full_text)
    parts = [p.strip() for p in parts if p and p.strip()]
    if len(parts) < 2:
        return []

    # 均匀分配词到各句（按字符数比例）
    total_chars = sum(len(p) for p in parts)
    if total_chars <= 0:
        return []

    segments: List[Dict[str, Any]] = []
    word_idx = 0
    for part in parts:
        ratio = len(part) / total_chars
        n_words = max(1, round(len(words) * ratio))
        chunk = words[word_idx:word_idx + n_words]
        if not chunk:
            break
        seg_start = chunk[0]["start"]
        seg_end = chunk[-1]["end"]
        segments.append({
            "start": round(seg_start, 3),
            "end": round(seg_end, 3),
            "text": part,
            "speaker": None,
        })
        word_idx += n_words

    # 修正最后一句的结束时间
    if segments:
        segments[-1]["end"] = round(words[-1]["end"], 3)

    return segments


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
        text = clean_text(str(seg.get("text") or seg.get("sentence") or ""))
        start = _to_seconds(seg.get("start"))
        end = _to_seconds(seg.get("end"))
        if start is None or end is None:
            ts_start, ts_end = _extract_seconds_from_timestamp_items(
                seg.get("timestamp") or seg.get("timestamps")
            )
            start = start if start is not None else ts_start
            end = end if end is not None else ts_end
        if text == "" or start is None or end is None or end < start:
            continue
        segments.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "text": text,
                "speaker": seg.get("spk", None),
            }
        )
    return segments


def build_segments_from_text(
    text: str,
    *,
    duration_s: float,
    clean_text: Callable[[str], str],
    max_segments: int = 200,
    start_offset: float = 0.0,
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
        start = start_offset + step * i
        end = start_offset + dur if i == n - 1 else (start_offset + step * (i + 1))
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
    # qwen3-asr 返回词级 timestamp 而非 sentence_info，用词级时间戳精确对齐分句
    timestamps = result0.get("timestamp")
    full_text = clean_text(result0.get("text", ""))
    if isinstance(timestamps, list) and len(timestamps) >= 2 and full_text:
        segs = _build_segments_from_word_timestamps(timestamps, full_text)
        if segs:
            return segs
    return build_segments_from_text(full_text, duration_s=duration_s, clean_text=clean_text)
