"""
程序说明：
提供转写结果的“输出渲染器”（纯后处理），将统一的 segments 结构渲染为：
txt / json / srt / vtt / tsv，并支持 all(zip) 打包输出。

设计原则：
- 与模型解耦：输入只依赖 segments[{start,end,text}] 与 full_text/meta
- 可测试：无需真实模型即可用 mock segments 验证输出
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any, Dict, List, Optional


def _clamp_seconds(value: Any) -> float:
    try:
        v = float(value)
    except Exception:
        return 0.0
    if v < 0:
        return 0.0
    return v


def _format_timestamp_srt(seconds: float) -> str:
    seconds = _clamp_seconds(seconds)
    total_ms = int(round(seconds * 1000.0))
    hh = total_ms // 3600000
    mm = (total_ms % 3600000) // 60000
    ss = (total_ms % 60000) // 1000
    ms = total_ms % 1000
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def _format_timestamp_vtt(seconds: float) -> str:
    seconds = _clamp_seconds(seconds)
    total_ms = int(round(seconds * 1000.0))
    hh = total_ms // 3600000
    mm = (total_ms % 3600000) // 60000
    ss = (total_ms % 60000) // 1000
    ms = total_ms % 1000
    return f"{hh:02d}:{mm:02d}:{ss:02d}.{ms:03d}"


def _wrap_text(text: str, max_line_width: Optional[int]) -> str:
    if not text:
        return ""
    if not max_line_width or max_line_width <= 0:
        return text

    lines: List[str] = []
    buf: List[str] = []
    width = 0

    for ch in text:
        if ch == "\n":
            lines.append("".join(buf))
            buf = []
            width = 0
            continue

        buf.append(ch)
        width += 1
        if width >= max_line_width:
            lines.append("".join(buf))
            buf = []
            width = 0

    if buf:
        lines.append("".join(buf))
    return "\n".join(lines)


def render_txt(segments: List[Dict[str, Any]], *, max_line_width: Optional[int] = None) -> str:
    parts: List[str] = []
    for seg in segments:
        seg_text = str(seg.get("text", "") or "").strip()
        if not seg_text:
            continue
        parts.append(_wrap_text(seg_text, max_line_width))
    return "\n\n".join(parts).strip() + ("\n" if parts else "")


def render_tsv(segments: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for seg in segments:
        start = _clamp_seconds(seg.get("start", 0.0))
        end = _clamp_seconds(seg.get("end", 0.0))
        text = str(seg.get("text", "") or "").replace("\n", " ").strip()
        lines.append(f"{start:.2f}\t{end:.2f}\t{text}")
    return "\n".join(lines).strip() + ("\n" if lines else "")


def render_srt(segments: List[Dict[str, Any]], *, max_line_width: Optional[int] = None) -> str:
    blocks: List[str] = []
    idx = 1
    for seg in segments:
        text = str(seg.get("text", "") or "").strip()
        if not text:
            continue
        start = _format_timestamp_srt(seg.get("start", 0.0))
        end = _format_timestamp_srt(seg.get("end", 0.0))
        body = _wrap_text(text, max_line_width)
        blocks.append(f"{idx}\n{start} --> {end}\n{body}\n")
        idx += 1
    if not blocks:
        return ""
    # SRT 通常要求每个 cue（包含最后一个）以空行结束，避免播放器兼容性问题
    return "\n".join(blocks) + "\n"


def render_vtt(segments: List[Dict[str, Any]], *, max_line_width: Optional[int] = None) -> str:
    blocks: List[str] = ["WEBVTT", ""]
    for seg in segments:
        text = str(seg.get("text", "") or "").strip()
        if not text:
            continue
        start = _format_timestamp_vtt(seg.get("start", 0.0))
        end = _format_timestamp_vtt(seg.get("end", 0.0))
        body = _wrap_text(text, max_line_width)
        blocks.append(f"{start} --> {end}\n{body}\n")
    return "\n".join(blocks).strip() + "\n"


def build_verbose_json_payload(
    *,
    full_text: str,
    segments: List[Dict[str, Any]],
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"text": full_text, "segments": segments}
    if meta:
        payload.update(meta)
    return payload


def render_json_pretty(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2).strip() + "\n"


def render_all_zip(
    *,
    full_text: str,
    segments: List[Dict[str, Any]],
    json_payload: Dict[str, Any],
    max_line_width: Optional[int] = None,
) -> bytes:
    txt = render_txt(segments, max_line_width=max_line_width)
    tsv = render_tsv(segments)
    srt = render_srt(segments, max_line_width=max_line_width)
    vtt = render_vtt(segments, max_line_width=max_line_width)
    js = render_json_pretty(json_payload)

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("output.txt", txt)
        zf.writestr("output.tsv", tsv)
        zf.writestr("output.srt", srt)
        zf.writestr("output.vtt", vtt)
        zf.writestr("output.json", js)
    return mem.getvalue()
