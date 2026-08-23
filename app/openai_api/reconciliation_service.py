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
from difflib import SequenceMatcher
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


def _join_segment_texts(parts: list[str]) -> str:
    """按时间拼接同一主段内的文本，英文词间补空格，中文保持紧凑。"""
    output = ""
    for raw_part in parts:
        part = str(raw_part or "").strip()
        if not part:
            continue
        if (
            output
            and output[-1].isascii()
            and output[-1].isalnum()
            and part[0].isascii()
            and part[0].isalnum()
        ):
            output += " "
        output += part
    return output


def _aggregate_overlapping_segments(
    anchor: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[str, int] | None:
    """聚合同一 reviewer 在主段范围内的全部重叠子段。"""
    matched = sorted(
        (
            (index, candidate)
            for index, candidate in enumerate(candidates)
            if _overlap_seconds(anchor, candidate) > 0
        ),
        key=lambda item: (
            float(item[1].get("start") or 0.0),
            float(item[1].get("end") or item[1].get("start") or 0.0),
            item[0],
        ),
    )
    if not matched:
        return None
    return _join_segment_texts([str(item.get("text") or "") for _, item in matched]), len(matched)


def _text_disagreement(left: str, right: str) -> float:
    """返回 0~1 的规范化文本差异率。"""
    if left == right:
        return 0.0
    if not left or not right:
        return 1.0
    return 1.0 - SequenceMatcher(None, left, right, autojunk=False).ratio()


def reconcile_transcriptions(
    primary: dict[str, Any],
    reviewers: list[dict[str, Any]],
    *,
    mode: str = "primary_first",
    disagreement_threshold: float = 0.2,
    keep_alternatives: bool = True,
    uncertain_policy: str = "flag_for_review",
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
            matched = _aggregate_overlapping_segments(
                primary_segment,
                list(reviewer.get("segments") or []),
            )
            if matched is None:
                continue
            matched_text, overlap_count = matched
            candidates.append(
                {
                    "model": str(reviewer.get("model") or "reviewer"),
                    "weight": float(reviewer.get("weight") or 1.0),
                    "text": matched_text,
                    "normalized": _normalize_text(matched_text),
                    "source": "reviewer",
                    "overlap_segment_count": overlap_count,
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

        # 近似文本先形成共识簇，簇内仍按原始变体权重选择可审计文本。
        clusters: list[dict[str, Any]] = []
        for key, group in groups.items():
            cluster = next(
                (
                    item
                    for item in clusters
                    if _text_disagreement(key, item["anchor_key"]) <= disagreement_threshold
                ),
                None,
            )
            if cluster is None:
                cluster = {"anchor_key": key, "keys": [], "weight": 0.0}
                clusters.append(cluster)
            cluster["keys"].append(key)
            cluster["weight"] += float(group["weight"])

        primary_key = candidates[0]["normalized"]
        selected_key = primary_key
        decision_rule = "primary_first"
        selected_cluster = next(item for item in clusters if primary_key in item["keys"])
        if mode == "weighted_consensus" and clusters:
            selected_cluster = max(
                clusters,
                key=lambda item: (item["weight"], primary_key in item["keys"]),
            )
            selected_key = max(
                selected_cluster["keys"],
                key=lambda key: (groups[key]["weight"], key == primary_key),
            )
            decision_rule = "weighted_consensus"

        uncertain = len(clusters) > 1
        if uncertain and uncertain_policy == "keep_primary":
            selected_key = primary_key
            selected_cluster = next(item for item in clusters if primary_key in item["keys"])
            decision_rule = "keep_primary_on_disagreement"

        selected_group = groups[selected_key]
        segment = deepcopy(primary_segment)
        segment["text"] = selected_group["text"]
        segment["selected_models"] = list(selected_group["models"])
        segment["decision_rule"] = decision_rule
        segment["uncertain"] = uncertain
        segment["disagreement_score"] = round(
            max(
                (_text_disagreement(selected_key, key) for key in groups if key != selected_key),
                default=0.0,
            ),
            4,
        )
        alternatives = [
            {
                "text": group["text"],
                "models": list(group["models"]),
                "weight": round(float(group["weight"]), 4),
                "disagreement": round(_text_disagreement(selected_key, key), 4),
            }
            for key, group in groups.items()
            if key != selected_key
        ]
        segment["alternatives"] = alternatives if keep_alternatives else []
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
        "disagreement_threshold": disagreement_threshold,
        "uncertain_policy": uncertain_policy,
    }
