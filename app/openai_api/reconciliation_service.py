"""
程序说明：
多模型转录候选的时间轴匹配与可审计共识选择。

原则：
- 默认保留主模型文本。
- 没有真实 confidence 时只使用用户权重和模型间一致性。
- 所有冲突保留候选、模型来源和决策规则。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _normalize_text(value: Any) -> str:
    return "".join(
        char.casefold()
        for char in str(value or "")
        if char.isalnum() or char == "'"
    )


def _overlap_seconds(segment_a: dict[str, Any], segment_b: dict[str, Any]) -> float:
    start_a = float(segment_a.get("start") or 0.0)
    end_a = float(segment_a.get("end") or start_a)
    start_b = float(segment_b.get("start") or 0.0)
    end_b = float(segment_b.get("end") or start_b)
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def _best_overlapping_segment(
    anchor: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    ranked = sorted(
        (
            (_overlap_seconds(anchor, candidate), candidate)
            for candidate in candidates
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] <= 0:
        return None
    return ranked[0][1]


def reconcile_transcriptions(
    primary: dict[str, Any],
    reviewers: list[dict[str, Any]],
    *,
    mode: str = "primary_first",
) -> dict[str, Any]:
    """在主模型时间轴上选择文本，并保留全部冲突证据。"""
    primary_model = str(primary.get("model") or "primary")
    primary_weight = float(primary.get("weight") or 1.0)
    output_segments: list[dict[str, Any]] = []

    for primary_segment in list(primary.get("segments") or []):
        candidates = [
            {
                "model": primary_model,
                "weight": primary_weight,
                "text": str(primary_segment.get("text") or ""),
                "normalized": _normalize_text(primary_segment.get("text")),
                "source": "primary",
            }
        ]
        for reviewer in reviewers:
            matched = _best_overlapping_segment(
                primary_segment,
                list(reviewer.get("segments") or []),
            )
            if matched is None:
                continue
            candidates.append(
                {
                    "model": str(reviewer.get("model") or "reviewer"),
                    "weight": float(reviewer.get("weight") or 1.0),
                    "text": str(matched.get("text") or ""),
                    "normalized": _normalize_text(matched.get("text")),
                    "source": "reviewer",
                }
            )

        groups: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            key = candidate["normalized"]
            group = groups.setdefault(
                key,
                {"weight": 0.0, "models": [], "text": candidate["text"]},
            )
            group["weight"] += candidate["weight"]
            group["models"].append(candidate["model"])

        primary_key = candidates[0]["normalized"]
        selected_key = primary_key
        decision_rule = "primary_first"
        if mode == "weighted_consensus" and groups:
            selected_key = max(
                groups,
                key=lambda key: (
                    groups[key]["weight"],
                    key == primary_key,
                ),
            )
            decision_rule = "weighted_consensus"

        selected_group = groups[selected_key]
        segment = deepcopy(primary_segment)
        segment["text"] = selected_group["text"]
        segment["selected_models"] = list(selected_group["models"])
        segment["decision_rule"] = decision_rule
        segment["uncertain"] = len(groups) > 1
        segment["alternatives"] = [
            {
                "text": group["text"],
                "models": list(group["models"]),
                "weight": round(float(group["weight"]), 4),
            }
            for key, group in groups.items()
            if key != selected_key
        ]
        segment["candidates"] = [
            {
                key: value
                for key, value in candidate.items()
                if key != "normalized"
            }
            for candidate in candidates
        ]
        output_segments.append(segment)

    return {
        "text": "".join(str(segment.get("text") or "") for segment in output_segments),
        "segments": output_segments,
        "primary_model": primary_model,
        "reviewer_models": [str(item.get("model") or "") for item in reviewers],
        "mode": mode,
    }
