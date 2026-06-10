"""
程序说明：验证独立 Gradio 麦克风诊断页的音频块解析逻辑。
"""

import importlib.util
import sys
import unittest
from pathlib import Path

import numpy as np


ROOT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT_DIR / "aipython" / "gradio_mic_test.py"
spec = importlib.util.spec_from_file_location("gradio_mic_test", MODULE_PATH)
gradio_mic_test = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gradio_mic_test
spec.loader.exec_module(gradio_mic_test)


class TestGradioMicTest(unittest.TestCase):
    def test_describe_gradio_audio_reports_int16_peak(self):
        status, peak, rms, samples = gradio_mic_test.describe_gradio_audio(
            (16000, np.array([0, 16384, -32768], dtype=np.int16))
        )

        self.assertIn("Python 已收到 Gradio 音频块", status)
        self.assertEqual(samples, 3)
        self.assertGreater(peak, 0.99)
        self.assertGreater(rms, 0.0)

    def test_stream_audio_counts_events(self):
        state, status, log = gradio_mic_test.stream_audio(
            (16000, np.array([0, 1000, -1000], dtype=np.int16)),
            {"count": 1},
        )

        self.assertEqual(state["count"], 2)
        self.assertIn("采样率：16000Hz", status)
        self.assertIn("stream #2", log)


if __name__ == "__main__":
    unittest.main()
