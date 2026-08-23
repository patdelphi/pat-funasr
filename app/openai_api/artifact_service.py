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
    artifacts: list[dict[str, Any]] = []

    render_map = {
        "json": lambda: renderers.render_json_pretty(payload),
        "txt": lambda: (
            renderers.render_txt(segments)
            if "".join(str(item.get("text") or "") for item in segments) == full_text
            else full_text.strip() + ("\n" if full_text.strip() else "")
        ),
        "srt": lambda: renderers.render_srt(segments),
        "vtt": lambda: renderers.render_vtt(segments),
        "tsv": lambda: renderers.render_tsv(segments),
    }
    for fmt in selected:
        path = root / f"transcript.{fmt}"
        _write_text(path, render_map[fmt]())
        artifacts.append(_artifact(path, fmt))

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
