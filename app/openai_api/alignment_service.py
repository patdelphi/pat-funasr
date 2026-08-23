"""
程序说明：
将说话人时间片对齐到统一转录时间轴。

原则：
- 只使用时间重叠证据，不根据文本猜测说话人。
- 多个 speaker 重叠接近时保留候选并标记不确定。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _overlap_seconds(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def align_speakers_to_segments(
    segments: list[dict[str, Any]],
    speaker_turns: list[dict[str, Any]],
    *,
    dominant_ratio: float = 0.55,
    tie_tolerance_s: float = 0.02,
) -> list[dict[str, Any]]:
    """按时间重叠为每个转录段分配 speaker 和候选。"""
    aligned: list[dict[str, Any]] = []
    for source_segment in segments:
        segment = deepcopy(source_segment)
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or start)
        overlap_by_speaker: dict[Any, float] = {}
        for turn in speaker_turns:
            speaker = turn.get("speaker")
            if speaker is None:
                continue
            overlap = _overlap_seconds(
                start,
                end,
                float(turn.get("start") or 0.0),
                float(turn.get("end") or 0.0),
            )
            if overlap > 0:
                overlap_by_speaker[speaker] = overlap_by_speaker.get(speaker, 0.0) + overlap

        ranked = sorted(
            overlap_by_speaker.items(),
            key=lambda item: (-item[1], str(item[0])),
        )
        segment["speaker_candidates"] = [item[0] for item in ranked]
        if not ranked:
            segment["speaker"] = None
            segment["speaker_uncertain"] = True
            segment["speaker_overlap_ratio"] = 0.0
            segment["alignment_quality"] = "unmatched"
            aligned.append(segment)
            continue

        total_overlap = sum(item[1] for item in ranked)
        top_speaker, top_overlap = ranked[0]
        second_overlap = ranked[1][1] if len(ranked) > 1 else 0.0
        top_ratio = top_overlap / total_overlap if total_overlap > 0 else 0.0
        tied = abs(top_overlap - second_overlap) <= float(tie_tolerance_s)
        confident = top_ratio >= float(dominant_ratio) and not tied
        segment["speaker"] = top_speaker if confident else None
        segment["speaker_uncertain"] = not confident
        segment["speaker_overlap_ratio"] = round(top_ratio, 4)
        segment["alignment_quality"] = "overlap"
        aligned.append(segment)
    return aligned
