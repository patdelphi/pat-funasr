"""
程序说明：
提供转写结果的分段与时长探测工具：
- 使用 ffprobe 获取音视频时长（秒）
- 优先使用结构化字词时间戳按标点生成句级时间轴
- 缺少可匹配时间戳时，按标点/长度切分并均匀分配时间
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
_SENTENCE_END_PATTERN = re.compile(r"(?<=[。！？!?；;])\s*|(?<=\.)(?!\d)\s*")


def _split_sentences(text: str) -> List[str]:
    """只按明确句末标点切分，不人为截断字词。"""
    value = (text or "").strip()
    if not value:
        return []
    return [part.strip() for part in _SENTENCE_END_PATTERN.split(value) if part.strip()]


def _split_text(text: str) -> List[str]:
    t = (text or "").strip()
    if not t:
        return []

    parts = _split_sentences(t)
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


def _normalize_alignment_text(value: Any) -> str:
    """对齐时忽略空白和标点，保留字母、数字、汉字及英文撇号。"""
    return "".join(
        ch.casefold()
        for ch in str(value or "")
        if ch.isalnum() or ch == "'"
    )


def build_segments_from_structured_timestamps(
    text: str,
    timestamps: Any,
    *,
    clean_text: Callable[[str], str],
) -> List[Dict[str, Any]]:
    """将 Qwen 强制对齐器的字词时间戳按原生标点聚合为句级时间轴。"""
    full_text = clean_text(text or "")
    parts = _split_sentences(full_text)
    if not full_text or not parts or not isinstance(timestamps, list):
        return []

    items: List[Dict[str, Any]] = []
    for item in timestamps:
        if not isinstance(item, dict):
            return []
        token = _normalize_alignment_text(item.get("text"))
        try:
            start = float(item.get("start_time"))
            end = float(item.get("end_time"))
        except (TypeError, ValueError):
            return []
        if not token or start < 0 or end < start:
            return []
        items.append({"token": token, "start": start, "end": end})

    normalized_text = _normalize_alignment_text(full_text)
    if not items or "".join(item["token"] for item in items) != normalized_text:
        return []

    segments: List[Dict[str, Any]] = []
    item_index = 0
    for part in parts:
        target = _normalize_alignment_text(part)
        if not target:
            continue
        first_index = item_index
        matched = ""
        while item_index < len(items) and len(matched) < len(target):
            candidate = matched + items[item_index]["token"]
            if not target.startswith(candidate):
                return []
            matched = candidate
            item_index += 1
        if matched != target:
            return []
        segments.append(
            {
                "start": round(items[first_index]["start"], 3),
                "end": round(items[item_index - 1]["end"], 3),
                "text": part,
                "speaker": None,
            }
        )

    return segments if item_index == len(items) else []


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

    # Qwen 原生文本已有标点，按结构化字词时间戳精确聚合句级边界。
    segments = build_segments_from_structured_timestamps(
        result0.get("text", ""),
        result0.get("timestamps"),
        clean_text=clean_text,
    )
    if segments:
        return segments

    # 结构化字词缺失或无法完全匹配时，保留原有稳定兜底。
    timestamps = result0.get("timestamp")
    if isinstance(timestamps, list) and timestamps:
        ts_start, ts_end = _extract_seconds_from_timestamp_items(timestamps)
        if ts_start is not None and ts_end is not None and ts_end > ts_start:
            return build_segments_from_text(
                result0.get("text", ""), duration_s=ts_end - ts_start, clean_text=clean_text,
                start_offset=round(ts_start, 3),
            )
    return build_segments_from_text(result0.get("text", ""), duration_s=duration_s, clean_text=clean_text)
