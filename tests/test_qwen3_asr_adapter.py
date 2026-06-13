"""
程序说明：
测试 Qwen3-ASR FunASR 适配层是否完整保留强制对齐器返回的字词与时间戳。
"""

import pathlib
import sys
import types
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from funasr.models.qwen3_asr.model import Qwen3ASR


class _FakeTranscriptionModel:
    forced_aligner = object()

    def transcribe(self, **kwargs):
        items = [
            types.SimpleNamespace(text="你", start_time=0.125, end_time=0.25),
            types.SimpleNamespace(text="好", start_time=0.3, end_time=0.45),
        ]
        return [
            types.SimpleNamespace(
                text="你好。",
                language="Chinese",
                time_stamps=types.SimpleNamespace(items=items),
            )
        ]


class TestQwen3ASRAdapter(unittest.TestCase):
    def test_inference_preserves_structured_and_legacy_timestamps(self):
        adapter = Qwen3ASR.__new__(Qwen3ASR)
        adapter.qwen3_asr_model = _FakeTranscriptionModel()

        result, _ = adapter.inference("sample.wav", key=["sample"], output_timestamp=True)

        self.assertEqual(result[0]["timestamp"], [[125, 250], [300, 450]])
        self.assertEqual(
            result[0]["timestamps"],
            [
                {"text": "你", "start_time": 0.125, "end_time": 0.25},
                {"text": "好", "start_time": 0.3, "end_time": 0.45},
            ],
        )


if __name__ == "__main__":
    unittest.main()
