"""
程序说明：验证独立 Gradio Mic FunASR 流式识别测试页的核心转换逻辑。
"""

import importlib.util
import sys
import unittest
from pathlib import Path

import numpy as np


ROOT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT_DIR / "aipython" / "gradio_streaming_asr_test.py"
spec = importlib.util.spec_from_file_location("gradio_streaming_asr_test", MODULE_PATH)
gradio_streaming_asr_test = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gradio_streaming_asr_test
spec.loader.exec_module(gradio_streaming_asr_test)


class TestGradioStreamingAsrTest(unittest.TestCase):
    def test_normalize_audio_converts_int16_to_float_mono(self):
        stereo = np.array([[0, 0], [16384, 16384], [-32768, -32768]], dtype=np.int16)

        sample_rate, samples = gradio_streaming_asr_test.normalize_audio((48000, stereo))

        self.assertEqual(sample_rate, 48000)
        self.assertEqual(samples.dtype, np.float32)
        np.testing.assert_allclose(samples, np.array([0.0, 0.5, -1.0], dtype=np.float32))

    def test_resample_to_16k_changes_length(self):
        samples = np.ones(48000, dtype=np.float32)

        down = gradio_streaming_asr_test.resample_to_16k(samples, 48000)

        self.assertEqual(down.shape[0], 16000)

    def test_multipart_streaming_body_contains_fields_and_pcm(self):
        pcm = b"\x00\x01\x02\x03"
        body, boundary = gradio_streaming_asr_test.multipart_streaming_body(
            {"model": "paraformer-zh-streaming", "reset": "true"},
            pcm,
        )

        self.assertIn(boundary.encode("utf-8"), body)
        self.assertIn(b'name="model"', body)
        self.assertIn(b"paraformer-zh-streaming", body)
        self.assertIn(pcm, body)

    def test_device_picker_html_patches_get_user_media(self):
        html = gradio_streaming_asr_test.DEVICE_PICKER_HTML
        js = gradio_streaming_asr_test.DEVICE_PICKER_JS

        self.assertIn("patMicDeviceSelect", html)
        self.assertIn("patRefreshMicDevices", html)
        self.assertNotIn("<script>", html)
        self.assertIn("enumerateDevices", js)
        self.assertIn("__patSelectedMicDeviceId", js)
        self.assertIn("patchedGetUserMedia", js)
        self.assertIn("deviceId = { exact: deviceId }", js)

    def test_start_and_stop_session_report_sent_count(self):
        state, transcript, status = gradio_streaming_asr_test.start_session(
            "http://127.0.0.1:8000",
            "paraformer-zh-streaming",
            "0,30,15",
        )
        state["sent"] = 3

        next_state, stop_status = gradio_streaming_asr_test.stop_session(state)

        self.assertEqual(transcript, "")
        self.assertIn("录音已开始", status)
        self.assertIs(next_state["sent"], state["sent"])
        self.assertIn("共发送 3 个", stop_status)


if __name__ == "__main__":
    unittest.main()
