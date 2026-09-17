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


_MAX_SECONDS = 100 * 3600  # 100 小时上限，防止 SRT/VTT 输出异常长时间范围


def _clamp_seconds(value: Any) -> float:
    try:
        v = float(value)
    except Exception:
        return 0.0
    if v < 0:
        return 0.0
    if v > _MAX_SECONDS:
        return float(_MAX_SECONDS)
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


def _with_speaker_prefix(seg: Dict[str, Any], text: str) -> str:
    speaker = seg.get("speaker", None)
    if speaker is None or text == "":
        return text
    return f"[spk={speaker}] {text}"


def _clean_segment_text(seg: Dict[str, Any]) -> str:
    """从 segment 提取 text 并清理：去首尾空白、内部 \\n\\n 合并、内部 \\n 变空格。

    ASR/LMM proofread 后 segment text 里可能残留 \\n（LLM 合并碎片时没清），
    直接放进 SRT/VTT 会导致 cue 里出现多行、甚至变成"孤儿行"破坏字幕结构。
    """
    text = str(seg.get("text", "") or "")
    # CRLF / LF 统一
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 多个换行 → 单空格
    import re as _re
    text = _re.sub(r"\n+", " ", text)
    # 多空格 → 单空格
    text = _re.sub(r" {2,}", " ", text)
    return text.strip()


def render_txt(segments: List[Dict[str, Any]], *, max_line_width: Optional[int] = None) -> str:
    """渲染带 speaker 标签的纯文本。

    分段策略（比原实现更智能）：
    - **同一个 speaker 连续的 segment 合并成一段**（用空格拼接）
    - **speaker 切换时开新段落**（空行分隔）
    - 解决原实现"3秒一刀切导致同 speaker 被切成十几段"的问题
    """
    if not segments:
        return ""

    # 先清理所有 text
    cleaned = [
        (_clean_segment_text(seg), seg.get("speaker"))
        for seg in segments
    ]
    cleaned = [(t, s) for t, s in cleaned if t]  # 丢掉空的

    parts: List[str] = []
    cur_speaker = None
    cur_buffer: List[str] = []

    for text, speaker in cleaned:
        if cur_speaker is None:
            # 第一段
            cur_speaker = speaker
            cur_buffer.append(text)
        elif speaker != cur_speaker:
            # speaker 切换 → flush 当前 buffer
            parts.append(_with_speaker_prefix(
                {"speaker": cur_speaker}, " ".join(cur_buffer)
            ))
            cur_speaker = speaker
            cur_buffer = [text]
        else:
            # 同 speaker → 追加
            cur_buffer.append(text)

    # flush 最后一段
    if cur_buffer:
        parts.append(_with_speaker_prefix(
            {"speaker": cur_speaker}, " ".join(cur_buffer)
        ))

    # 应用行宽
    parts = [_wrap_text(p, max_line_width) for p in parts]
    return "\n\n".join(parts).strip() + "\n"


def render_tsv(segments: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for seg in segments:
        start = _clamp_seconds(seg.get("start", 0.0))
        end = _clamp_seconds(seg.get("end", 0.0))
        text = _clean_segment_text(seg)
        text = _with_speaker_prefix(seg, text)
        lines.append(f"{start:.2f}\t{end:.2f}\t{text}")
    return "\n".join(lines).strip() + ("\n" if lines else "")


def render_csv(segments: List[Dict[str, Any]]) -> str:
    """渲染 CSV（Excel 友好）：表头 start,end,speaker,text，逐段一行。

    用 csv 模块处理逗号/引号转义；换行统一 LF，由调用方按需转 CRLF/BOM。
    """
    import csv as _csv

    buffer = io.StringIO()
    writer = _csv.writer(buffer, lineterminator="\n")
    writer.writerow(["start", "end", "speaker", "text"])
    for seg in segments:
        start = _clamp_seconds(seg.get("start", 0.0))
        end = _clamp_seconds(seg.get("end", 0.0))
        speaker = seg.get("speaker")
        writer.writerow([
            f"{start:.2f}",
            f"{end:.2f}",
            "" if speaker is None else str(speaker),
            _clean_segment_text(seg),
        ])
    return buffer.getvalue()


# 最小 DOCX 所需的静态部件（纯标准库 zipfile 拼装，避免引入 python-docx 依赖）
_DOCX_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_DOCX_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)


def _xml_escape_text(text: str) -> str:
    """剔除 XML 1.0 非法控制字符并转义 & < > "。"""
    import re as _re

    text = _re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text))
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_docx(segments: List[Dict[str, Any]], *, title: str = "转写文本") -> bytes:
    """渲染最小合法 DOCX（纯标准库）：标题 + 每段一行 '[起 --> 止] [spk=N] 文本'。"""
    paragraphs: List[str] = []
    # 标题段落（加粗）
    paragraphs.append(
        '<w:p><w:r><w:rPr><w:b/></w:rPr>'
        f'<w:t xml:space="preserve">{_xml_escape_text(title)}</w:t></w:r></w:p>'
    )
    for seg in segments:
        text = _clean_segment_text(seg)
        if not text:
            continue
        start = _format_timestamp_srt(seg.get("start", 0.0))
        end = _format_timestamp_srt(seg.get("end", 0.0))
        speaker = seg.get("speaker")
        prefix = f"[{start} --> {end}]"
        if speaker is not None:
            prefix += f" [spk={speaker}]"
        paragraphs.append(
            "<w:p><w:r>"
            f'<w:t xml:space="preserve">{_xml_escape_text(prefix + " " + text)}</w:t>'
            "</w:r></w:p>"
        )
    body = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(paragraphs)
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
        'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES)
        zf.writestr("_rels/.rels", _DOCX_RELS)
        zf.writestr("word/document.xml", body)
    return mem.getvalue()


def render_srt(segments: List[Dict[str, Any]], *, max_line_width: Optional[int] = None) -> str:
    blocks: List[str] = []
    idx = 1
    for seg in segments:
        text = _clean_segment_text(seg)
        if not text:
            continue
        text = _with_speaker_prefix(seg, text)
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
        text = _clean_segment_text(seg)
        if not text:
            continue
        text = _with_speaker_prefix(seg, text)
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
    timestamp: str | None = None,
    extra_formats: Optional[List[str]] = None,
) -> bytes:
    """
    基础 5 件套 ZIP（离线识别/说话人分离），产物命名与 render_fine_all_zip 对齐：
      transcript_{ts}.json / .txt / .srt / .vtt / .tsv
    文本统一 UTF-8 BOM + CRLF。

    extra_formats 可选追加 csv/docx（显式选择，不改变默认 5 件套）。
    """
    from artifact_service import _ts_name

    def _crlf(text: str) -> bytes:
        normalized = str(text).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        return b"\xef\xbb\xbf" + normalized.encode("utf-8")

    seg_txt = render_txt(segments, max_line_width=max_line_width)
    full_text = str(full_text or "")
    # transcript.txt 优先级：render_txt(segments) > full_text
    # render_all_zip 无 LLM proofread，不需要 refined_text 分支
    plain_txt = seg_txt if seg_txt.strip() else (full_text.strip() + "\r\n")

    extras = set(extra_formats or ())

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_ts_name("transcript", "json", timestamp), _crlf(render_json_pretty(json_payload)))
        zf.writestr(_ts_name("transcript", "txt", timestamp), _crlf(plain_txt))
        zf.writestr(_ts_name("transcript", "srt", timestamp), _crlf(render_srt(segments, max_line_width=max_line_width)))
        zf.writestr(_ts_name("transcript", "vtt", timestamp), _crlf(render_vtt(segments, max_line_width=max_line_width)))
        zf.writestr(_ts_name("transcript", "tsv", timestamp), _crlf(render_tsv(segments)))
        if "csv" in extras:
            zf.writestr(_ts_name("transcript", "csv", timestamp), _crlf(render_csv(segments)))
        if "docx" in extras:
            # DOCX 是二进制，不做 BOM/CRLF 转换
            zf.writestr(_ts_name("transcript", "docx", timestamp), render_docx(segments))
    return mem.getvalue()


def render_fine_all_zip(
    *,
    full_text: str,
    refined_text: str = "",
    segments: List[Dict[str, Any]],
    json_payload: Dict[str, Any],
    summary: Optional[Dict[str, Any]] = None,
    mindmap: Optional[Dict[str, Any]] = None,
    scene_name: str = "",
    elapsed: float = 0.0,
    max_line_width: Optional[int] = None,
    timestamp: str | None = None,
    extra_formats: Optional[List[str]] = None,
) -> bytes:
    """
    精细转录打包 ZIP，产物列表与 artifact_service.write_workflow_artifacts 完全对齐：

      transcript_{ts}.json / .txt / .srt / .vtt / .tsv
      transcript_refined_{ts}.txt（仅当 refined ≠ full 时）
      summary_{ts}.md（复用 artifact_service._render_summary_markdown）
      mindmap_{ts}.json

    文本统一 UTF-8 BOM + CRLF。
    """
    import json as _json_mod
    from artifact_service import _render_summary_markdown, _ts_name

    def _crlf(text: str) -> bytes:
        normalized = str(text).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        return b"\xef\xbb\xbf" + normalized.encode("utf-8")

    seg_txt = render_txt(segments, max_line_width=max_line_width)
    full_text = str(full_text or "")
    refined_text = str(refined_text or "")
    # transcript.txt 优先级：refined_text > render_txt(segments) > full_text
    # 只要 LLM proofread 产生了 refined_text，就优先用
    if refined_text.strip():
        plain_txt = refined_text.strip() + "\r\n"
    elif seg_txt.strip():
        plain_txt = seg_txt
    else:
        plain_txt = full_text.strip() + "\r\n"

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_ts_name("transcript", "json", timestamp), _crlf(render_json_pretty(json_payload)))
        zf.writestr(_ts_name("transcript", "txt", timestamp), _crlf(plain_txt))
        zf.writestr(_ts_name("transcript", "srt", timestamp), _crlf(render_srt(segments, max_line_width=max_line_width)))
        zf.writestr(_ts_name("transcript", "vtt", timestamp), _crlf(render_vtt(segments, max_line_width=max_line_width)))
        zf.writestr(_ts_name("transcript", "tsv", timestamp), _crlf(render_tsv(segments)))

        # 校对后全文（与原文不同才额外写出）
        if refined_text and refined_text != full_text:
            zf.writestr(_ts_name("transcript_refined", "txt", timestamp), _crlf(refined_text))

        # 纪要 Markdown（复用 artifact_service 统一渲染）
        if summary:
            md = _render_summary_markdown(summary)
            if md.strip():
                zf.writestr(_ts_name("summary", "md", timestamp), _crlf(md))

        # 脑图 JSON
        if mindmap:
            zf.writestr(_ts_name("mindmap", "json", timestamp), _crlf(_json_mod.dumps(mindmap, ensure_ascii=False, indent=2) + "\n"))

        # 可选追加 csv/docx（显式选择，不改变默认 8 件套）
        extras = set(extra_formats or ())
        if "csv" in extras:
            zf.writestr(_ts_name("transcript", "csv", timestamp), _crlf(render_csv(segments)))
        if "docx" in extras:
            zf.writestr(_ts_name("transcript", "docx", timestamp), render_docx(segments))
    return mem.getvalue()
