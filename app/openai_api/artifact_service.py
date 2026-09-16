"""
程序说明：
统一写出精细转录工作流产物，并保留配置快照和事件日志。

所有文本产物统一使用 UTF-8 BOM 与 CRLF，便于 Windows 工具直接打开。
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import renderers


_TRANSCRIPT_FORMATS = ("json", "txt", "srt", "vtt", "tsv")


def _make_timestamp() -> str:
    """生成本次产物统一的时间戳。可通过模块级 _TEST_TS 覆盖（供测试用）。"""
    ts = globals().get("_TEST_TS")
    if ts:
        return ts
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _ts_name(stem: str, suffix: str, timestamp: str | None) -> str:
    """生成带时间戳的文件名，如 transcript_20260903_180245.json。"""
    ext = suffix if suffix.startswith(".") else f".{suffix}"
    if timestamp:
        return f"{stem}_{timestamp}{ext}"
    return f"{stem}{ext}"


def _ts_stem(filename: str) -> tuple[str, str] | None:
    """从带时间戳的文件名中提取 (stem, suffix)。

    例: "transcript_20260903_180245.json"      → ("transcript", "json")
        "transcript_refined_20260903_180245.txt" → ("transcript_refined", "txt")
        "summary_20260903_180245.md"            → ("summary", "md")
        "plain_old.json"                        → None
    """
    import re as _re
    m = _re.match(r"^(.+)_\d{8}_\d{6}\.([^.]+)$", filename)
    if m:
        return m.group(1).lower(), m.group(2).lower()
    return None


def _crlf_bytes(text: str, *, add_bom: bool = False) -> bytes:
    """统一 CRLF 换行。BOM 仅对 Excel 友好的文本格式启用，JSON/JSONL/MD 禁止。"""
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    data = normalized.encode("utf-8")
    if add_bom:
        data = b"\xef\xbb\xbf" + data
    return data


def _write_text(path: Path, text: str) -> None:
    """根据扩展名决定是否加 BOM。"""
    ext = path.suffix.lower().lstrip(".")
    # Excel 友好格式加 BOM：txt / tsv / csv / srt / vtt
    bom_exts = {"txt", "tsv", "csv", "srt", "vtt"}
    path.write_bytes(_crlf_bytes(text, add_bom=(ext in bom_exts)))


# reconciliation / multi-model 内部字段（JSON 导出时要删掉，用户不需要看）
_INTERNAL_SEGMENT_FIELDS = frozenset([
    "candidates", "alternatives",          # 多模型候选原文
    "alignment_quality",                    # 强制对齐置信度
    "decision_rule",                        # reconcile 决策规则
    "disagreement_score",                   # 多模型分歧分数
    "selected_models",                      # 最终选中的模型名
    "speaker_candidates",                   # 声纹候选列表
    "speaker_overlap_ratio",                # 说话重叠比例
    "speaker_uncertain",                    # 说话人不确定标志
    "uncertain",                            # 多模型不确定标志
])

_INTERNAL_TOP_LEVEL_FIELDS = frozenset([
    "model_runs",                           # 每次推理的完整日志
    "original_text",                        # ASR 原始全文（没润色的）
])


def _public_result(result: dict[str, Any], include_raw_candidates: bool) -> dict[str, Any]:
    """把内部 result dict 裁成面向终端用户的公开版本。"""
    payload = copy.deepcopy(result)
    payload.pop("artifacts", None)
    if include_raw_candidates:
        # 只保留 artifacts，其他正常字段都在
        return payload
    # 顶级内部字段
    for key in _INTERNAL_TOP_LEVEL_FIELDS:
        payload.pop(key, None)
    # segment 级内部字段
    for segment in payload.get("segments") or []:
        if isinstance(segment, dict):
            for key in _INTERNAL_SEGMENT_FIELDS:
                segment.pop(key, None)
    return payload


def _render_summary_markdown(summary_obj: Any) -> str:
    """把 summary dict 渲染成 Markdown 纪要文本。

    支持两种结构：
    - **新结构**（LLM 二次聚合后）：meeting_title / participants / overall_summary / sections[] / open_questions / next_steps
    - **旧结构**（兜底直拼）：{"parts": [ {summary, decisions, action_items, notes}, ... ]}
    """
    if not summary_obj:
        return ""
    if isinstance(summary_obj, str):
        return summary_obj

    lines: list[str] = []

    # === 新结构：二次聚合后的完整纪要 ===
    if isinstance(summary_obj, dict) and (
        summary_obj.get("sections") or summary_obj.get("overall_summary") or summary_obj.get("meeting_title")
    ):
        meeting_title = str(summary_obj.get("meeting_title") or "会议纪要").strip()
        lines.append(f"# {meeting_title}")
        lines.append("")

        participants = summary_obj.get("participants") or []
        if participants and isinstance(participants, list):
            # 从 dict 提取可读名字：优先 role，fallback spk，最后 str()
            def _fmt_participant(p):
                if isinstance(p, dict):
                    role = str(p.get("role") or "").strip()
                    spk = str(p.get("spk") or "").strip()
                    if role and spk:
                        return f"{role} ({spk})"
                    return role or spk or ""
                return str(p).strip()
            names = [_fmt_participant(p) for p in participants]
            names = [n for n in names if n]
            if names:
                lines.append(f"**参会人员**：{'、'.join(names)}")
                lines.append("")

        overall = str(summary_obj.get("overall_summary") or "").strip()
        if overall:
            lines.append("## 会议总览")
            lines.append("")
            lines.append(overall)
            lines.append("")

        # 按议题渲染
        sections = summary_obj.get("sections") or []
        if sections and isinstance(sections, list):
            lines.append("## 议题讨论")
            lines.append("")
            for sec in sections:
                if not isinstance(sec, dict):
                    continue
                topic = str(sec.get("topic") or "").strip()
                if not topic:
                    continue
                lines.append(f"### {topic}")
                lines.append("")
                sec_summary = str(sec.get("summary") or "").strip()
                if sec_summary:
                    lines.append(sec_summary)
                    lines.append("")

                # 关键要点
                key_points = sec.get("key_points") or []
                if key_points and isinstance(key_points, list):
                    for kp in key_points:
                        if str(kp).strip():
                            lines.append(f"- {str(kp).strip()}")
                    lines.append("")

                # 决定（议题级）
                decisions = sec.get("decisions") or []
                if decisions and isinstance(decisions, list):
                    for d in decisions:
                        if isinstance(d, str) and d.strip():
                            lines.append(f"- ✅ {d.strip()}")
                        elif isinstance(d, dict):
                            pt = str(d.get("decision_point") or "").strip()
                            desc = str(d.get("description") or "").strip()
                            if pt:
                                lines.append(f"- ✅ **{pt}**：{desc}")
                            elif desc:
                                lines.append(f"- ✅ {desc}")
                    lines.append("")

                # 行动项（议题级）
                actions = sec.get("action_items") or []
                if actions and isinstance(actions, list):
                    for a in actions:
                        if isinstance(a, dict):
                            task = str(a.get("task") or "").strip()
                            owner = str(a.get("owner") or "").strip()
                            dl = str(a.get("deadline") or "").strip()
                            if task:
                                suf = ""
                                if owner or dl:
                                    suf = f"（{owner}" + (f"，{dl}）" if dl else "）")
                                lines.append(f"- [ ] {task}{suf}")
                        elif isinstance(a, str) and a.strip():
                            lines.append(f"- [ ] {a.strip()}")
                    lines.append("")

                # 风险/待确认（议题级）
                risks = sec.get("risks_or_todos") or []
                if risks and isinstance(risks, list):
                    for r in risks:
                        if str(r).strip():
                            lines.append(f"- ⚠️ {str(r).strip()}")
                    lines.append("")

        # 全局行动项
        next_steps = summary_obj.get("next_steps") or []
        if next_steps and isinstance(next_steps, list):
            lines.append("## 后续步骤")
            lines.append("")
            for ns in next_steps:
                if isinstance(ns, str) and ns.strip():
                    lines.append(f"- [ ] {ns.strip()}")
                elif isinstance(ns, dict):
                    task = str(ns.get("task") or "").strip()
                    owner = str(ns.get("owner") or "").strip()
                    dl = str(ns.get("deadline") or "").strip()
                    if task:
                        suf = ""
                        if owner or dl:
                            suf = f"（{owner}" + (f"，{dl}）" if dl else "）")
                        lines.append(f"- [ ] {task}{suf}")
            lines.append("")

        # 会后待确认
        open_q = summary_obj.get("open_questions") or []
        if open_q and isinstance(open_q, list):
            qs = [str(q).strip() for q in open_q if str(q).strip()]
            if qs:
                lines.append("## 待确认问题")
                lines.append("")
                for q in qs:
                    lines.append(f"- ❓ {q}")
                lines.append("")

        return "\r\n".join(lines).strip() + "\r\n"

    # === 旧结构兜底：parts 直接拼接 ===
    parts = summary_obj.get("parts", []) if isinstance(summary_obj, dict) else []
    if not parts:
        # 既不是新结构也没有 parts，直接 dump 一下
        return json.dumps(summary_obj, ensure_ascii=False, indent=2) + "\r\n"

    lines.append("# 会议纪要")
    lines.append("")

    for idx, part in enumerate(parts, 1):
        if not isinstance(part, dict):
            continue
        summary_text = str(part.get("summary") or "").strip()
        if summary_text:
            heading = "## 摘要" if len(parts) == 1 else f"## 摘要（第 {idx} 部分）"
            lines.append(heading)
            lines.append("")
            lines.append(summary_text)
            lines.append("")

        decisions = part.get("decisions") or []
        if decisions and isinstance(decisions, list):
            lines.append("### 决定")
            lines.append("")
            for d in decisions:
                if isinstance(d, str) and d.strip():
                    lines.append(f"- {d.strip()}")
            lines.append("")

        actions = part.get("action_items") or []
        if actions and isinstance(actions, list):
            lines.append("### 行动项")
            lines.append("")
            for a in actions:
                if isinstance(a, str) and a.strip():
                    lines.append(f"- [ ] {a.strip()}")
                elif isinstance(a, dict):
                    task = str(a.get("task") or "").strip()
                    if task:
                        lines.append(f"- [ ] {task}")
            lines.append("")

        notes = str(part.get("notes") or "").strip()
        if notes:
            lines.append("### 备注")
            lines.append("")
            lines.append(notes)
            lines.append("")

    return "\r\n".join(lines).strip() + "\r\n"


def write_workflow_artifacts(
    *,
    output_dir: str | Path,
    result: dict[str, Any],
    config: dict[str, Any],
    events: list[dict[str, Any]],
    formats: list[str],
    include_raw_candidates: bool,
    include_config_snapshot: bool,
    timestamp: str | None = None,
) -> list[dict[str, Any]]:
    """写出所选转录格式及审计文件，返回可下载产物清单。"""
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    selected: list[str] = []
    for item in formats:
        expanded = _TRANSCRIPT_FORMATS if item == "all" else (item,)
        for fmt in expanded:
            if fmt in _TRANSCRIPT_FORMATS and fmt not in selected:
                selected.append(fmt)

    payload = _public_result(result, include_raw_candidates)
    segments = list(payload.get("segments") or [])
    full_text = str(payload.get("text") or "")
    refined_text = str(payload.get("refined_text") or full_text)
    summary = payload.get("summary")
    mindmap = payload.get("mindmap")
    artifacts: list[dict[str, Any]] = []

    # transcript.txt：永远用 render_txt(segments)，保留说话人 [spk=N] 和段落空行
    # refined_text 作为 transcript_refined.txt 单独导出（见下）
    seg_txt = renderers.render_txt(segments)
    plain_txt = seg_txt if seg_txt.strip() else (full_text.strip() + ("\n" if full_text.strip() else ""))

    render_map = {
        "json": lambda: renderers.render_json_pretty(payload),
        "txt": lambda: plain_txt,
        "srt": lambda: renderers.render_srt(segments),
        "vtt": lambda: renderers.render_vtt(segments),
        "tsv": lambda: renderers.render_tsv(segments),
    }
    for fmt in selected:
        path = root / _ts_name("transcript", fmt, timestamp)
        _write_text(path, render_map[fmt]())
        artifacts.append(_artifact(path, fmt))

    # 校对后全文（如果与原文不同则单独导出）
    if refined_text and refined_text != full_text:
        refined_path = root / _ts_name("transcript_refined", "txt", timestamp)
        _write_text(refined_path, refined_text)
        artifacts.append(_artifact(refined_path, "txt"))

    # 纪要 Markdown
    if summary:
        summary_md = _render_summary_markdown(summary)
        if summary_md.strip():
            sum_path = root / _ts_name("summary", "md", timestamp)
            _write_text(sum_path, summary_md)
            artifacts.append(_artifact(sum_path, "md"))

    # 脑图 JSON
    if mindmap:
        mm_path = root / _ts_name("mindmap", "json", timestamp)
        _write_text(mm_path, json.dumps(mindmap, ensure_ascii=False, indent=2) + "\n")
        artifacts.append(_artifact(mm_path, "json"))

    if include_config_snapshot:
        config_path = root / _ts_name("workflow-config", "json", timestamp)
        _write_text(config_path, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
        artifacts.append(_artifact(config_path, "json"))

    events_path = root / _ts_name("events", "jsonl", timestamp)
    event_text = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in events)
    _write_text(events_path, event_text)
    artifacts.append(_artifact(events_path, "jsonl"))
    return artifacts


def _artifact(path: Path, fmt: str) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "name": resolved.name,
        "format": fmt,
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
    }


def refresh_events_artifact(snapshot: dict[str, Any]) -> None:
    """任务进入终态后重写事件产物，确保包含导出成功和任务完成事件。"""
    artifacts = (snapshot.get("result") or {}).get("artifacts") or []
    artifact = next(
        (item for item in artifacts if str(item.get("name") or "").endswith(".jsonl")),
        None,
    )
    if artifact is None:
        return
    path = Path(str(artifact.get("path") or "")).resolve()
    if path.suffix != ".jsonl" or not path.parent.is_dir():
        return
    event_text = "".join(
        json.dumps(item, ensure_ascii=False) + "\n"
        for item in snapshot.get("events") or []
    )
    _write_text(path, event_text)
