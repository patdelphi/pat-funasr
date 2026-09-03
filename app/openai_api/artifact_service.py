"""
程序说明：
统一写出精细转录工作流产物，并保留配置快照和事件日志。

所有文本产物统一使用 UTF-8 BOM 与 CRLF，便于 Windows 工具直接打开。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import renderers


_TRANSCRIPT_FORMATS = ("json", "txt", "srt", "vtt", "tsv")


def _crlf_bytes(text: str) -> bytes:
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    return b"\xef\xbb\xbf" + normalized.encode("utf-8")


def _write_text(path: Path, text: str) -> None:
    path.write_bytes(_crlf_bytes(text))


def _public_result(result: dict[str, Any], include_raw_candidates: bool) -> dict[str, Any]:
    payload = copy.deepcopy(result)
    payload.pop("artifacts", None)
    if include_raw_candidates:
        return payload
    payload.pop("model_runs", None)
    for segment in payload.get("segments") or []:
        if isinstance(segment, dict):
            segment.pop("candidates", None)
            segment.pop("alternatives", None)
    return payload


def _render_summary_markdown(summary_obj: Any) -> str:
    """把 summary dict 渲染成 Markdown 纪要文本。"""
    if not summary_obj:
        return ""
    if isinstance(summary_obj, str):
        return summary_obj

    parts = summary_obj.get("parts", []) if isinstance(summary_obj, dict) else []
    lines: list[str] = ["# 会议纪要", ""]

    for idx, part in enumerate(parts, 1):
        if not isinstance(part, dict):
            continue
        # 摘要
        summary_text = str(part.get("summary") or "").strip()
        if summary_text:
            if len(parts) > 1:
                lines.append(f"## 摘要（第 {idx} 部分）")
            else:
                lines.append("## 摘要")
            lines.append("")
            lines.append(summary_text)
            lines.append("")

        # 决定
        decisions = part.get("decisions") or []
        if decisions and isinstance(decisions, list):
            lines.append("### 决定")
            lines.append("")
            for d in decisions:
                if isinstance(d, dict):
                    point = str(d.get("decision_point") or "").strip()
                    desc = str(d.get("description") or "").strip()
                    if point:
                        lines.append(f"- **{point}**：{desc}")
                    elif desc:
                        lines.append(f"- {desc}")
                elif isinstance(d, str) and d.strip():
                    lines.append(f"- {d.strip()}")
            lines.append("")

        # 行动项
        actions = part.get("action_items") or []
        if actions and isinstance(actions, list):
            lines.append("### 行动项")
            lines.append("")
            for a in actions:
                if isinstance(a, dict):
                    task = str(a.get("task") or a.get("action") or "").strip()
                    owner = str(a.get("owner") or a.get("person") or "").strip()
                    deadline = str(a.get("deadline") or a.get("due") or "").strip()
                    if task:
                        suffix = ""
                        if owner or deadline:
                            suffix = f"（{owner}" + (f"，{deadline}）" if deadline else "）")
                        lines.append(f"- [ ] {task}{suffix}")
                elif isinstance(a, str) and a.strip():
                    lines.append(f"- [ ] {a.strip()}")
            lines.append("")

        # 备注
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

    # transcript.txt 选择优先级：refined_text > render_txt(segments) > full_text
    # 只要 LLM proofread 产生了 refined_text，就优先用（segments 可能还没同步更新）
    refined_text = str(payload.get("refined_text") or "")
    full_text = str(payload.get("text") or "")
    seg_txt = renderers.render_txt(segments)
    if refined_text.strip():
        plain_txt = refined_text.strip() + "\n"
    elif seg_txt.strip():
        plain_txt = seg_txt
    else:
        plain_txt = full_text.strip() + ("\n" if full_text.strip() else "")

    render_map = {
        "json": lambda: renderers.render_json_pretty(payload),
        "txt": lambda: plain_txt,
        "srt": lambda: renderers.render_srt(segments),
        "vtt": lambda: renderers.render_vtt(segments),
        "tsv": lambda: renderers.render_tsv(segments),
    }
    for fmt in selected:
        path = root / f"transcript.{fmt}"
        _write_text(path, render_map[fmt]())
        artifacts.append(_artifact(path, fmt))

    # 校对后全文（如果与原文不同则单独导出）
    if refined_text and refined_text != full_text:
        refined_path = root / "transcript_refined.txt"
        _write_text(refined_path, refined_text)
        artifacts.append(_artifact(refined_path, "txt"))

    # 纪要 Markdown
    if summary:
        summary_md = _render_summary_markdown(summary)
        if summary_md.strip():
            sum_path = root / "summary.md"
            _write_text(sum_path, summary_md)
            artifacts.append(_artifact(sum_path, "md"))

    # 脑图 JSON
    if mindmap:
        mm_path = root / "mindmap.json"
        _write_text(mm_path, json.dumps(mindmap, ensure_ascii=False, indent=2) + "\n")
        artifacts.append(_artifact(mm_path, "json"))

    if include_config_snapshot:
        config_path = root / "workflow-config.json"
        _write_text(config_path, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
        artifacts.append(_artifact(config_path, "json"))

    events_path = root / "events.jsonl"
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
        (item for item in artifacts if item.get("name") == "events.jsonl"),
        None,
    )
    if artifact is None:
        return
    path = Path(str(artifact.get("path") or "")).resolve()
    if path.name != "events.jsonl" or not path.parent.is_dir():
        return
    event_text = "".join(
        json.dumps(item, ensure_ascii=False) + "\n"
        for item in snapshot.get("events") or []
    )
    _write_text(path, event_text)
