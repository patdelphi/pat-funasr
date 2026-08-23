"""
程序说明：
说话人时间轴对齐和多模型转录共识测试。

目标：
- speaker 只依据时间重叠分配，边界不确定时保留候选而不猜测。
- 多模型冲突保留候选和决策证据。
- 加权共识只有在候选权重占优时替换主模型文本。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_OPENAI_API_DIR = _ROOT / "app" / "openai_api"
if str(_OPENAI_API_DIR) not in sys.path:
    sys.path.insert(0, str(_OPENAI_API_DIR))

from alignment_service import align_speakers_to_segments  # noqa: E402
from reconciliation_service import reconcile_transcriptions  # noqa: E402


class TestSpeakerAlignment(unittest.TestCase):
    def test_assigns_speaker_with_largest_overlap(self):
        segments = [{"start": 0.0, "end": 2.0, "text": "你好"}]
        turns = [
            {"start": 0.0, "end": 1.6, "speaker": 0},
            {"start": 1.6, "end": 2.0, "speaker": 1},
        ]
        result = align_speakers_to_segments(segments, turns)
        self.assertEqual(result[0]["speaker"], 0)
        self.assertEqual(result[0]["speaker_candidates"], [0, 1])
        self.assertEqual(result[0]["alignment_quality"], "overlap")

    def test_equal_overlap_remains_uncertain(self):
        segments = [{"start": 0.0, "end": 2.0, "text": "交叉发言"}]
        turns = [
            {"start": 0.0, "end": 1.0, "speaker": 0},
            {"start": 1.0, "end": 2.0, "speaker": 1},
        ]
        result = align_speakers_to_segments(segments, turns)
        self.assertIsNone(result[0]["speaker"])
        self.assertTrue(result[0]["speaker_uncertain"])
        self.assertEqual(result[0]["speaker_candidates"], [0, 1])

    def test_no_overlap_preserves_null_speaker(self):
        result = align_speakers_to_segments(
            [{"start": 0.0, "end": 1.0, "text": "静音前"}],
            [{"start": 2.0, "end": 3.0, "speaker": 0}],
        )
        self.assertIsNone(result[0]["speaker"])
        self.assertEqual(result[0]["alignment_quality"], "unmatched")


class TestReconciliation(unittest.TestCase):
    def test_primary_first_keeps_primary_and_records_alternative(self):
        primary = {
            "model": "primary",
            "weight": 1.0,
            "segments": [{"start": 0.0, "end": 1.0, "text": "项目明天上线"}],
        }
        reviewer = {
            "model": "reviewer",
            "weight": 0.8,
            "segments": [{"start": 0.0, "end": 1.0, "text": "项目明日上线"}],
        }
        result = reconcile_transcriptions(primary, [reviewer], mode="primary_first")
        segment = result["segments"][0]
        self.assertEqual(segment["text"], "项目明天上线")
        self.assertTrue(segment["uncertain"])
        self.assertEqual(segment["alternatives"][0]["text"], "项目明日上线")
        self.assertEqual(segment["decision_rule"], "primary_first")

    def test_weighted_consensus_selects_matching_reviewers(self):
        primary = {
            "model": "primary",
            "weight": 1.0,
            "segments": [{"start": 0.0, "end": 1.0, "text": "项目明天上线"}],
        }
        reviewers = [
            {
                "model": "reviewer-a",
                "weight": 0.8,
                "segments": [{"start": 0.0, "end": 1.0, "text": "项目明日上线"}],
            },
            {
                "model": "reviewer-b",
                "weight": 0.8,
                "segments": [{"start": 0.0, "end": 1.0, "text": "项目明日上线"}],
            },
        ]
        result = reconcile_transcriptions(primary, reviewers, mode="weighted_consensus")
        segment = result["segments"][0]
        self.assertEqual(segment["text"], "项目明日上线")
        self.assertEqual(segment["selected_models"], ["reviewer-a", "reviewer-b"])
        self.assertEqual(segment["decision_rule"], "weighted_consensus")

    def test_identical_candidates_are_not_uncertain(self):
        primary = {
            "model": "primary",
            "weight": 1.0,
            "segments": [{"start": 0.0, "end": 1.0, "text": "一致文本"}],
        }
        reviewer = {
            "model": "reviewer",
            "weight": 0.5,
            "segments": [{"start": 0.0, "end": 1.0, "text": "一致文本。"}],
        }
        result = reconcile_transcriptions(primary, [reviewer], mode="weighted_consensus")
        self.assertFalse(result["segments"][0]["uncertain"])


if __name__ == "__main__":
    unittest.main()
