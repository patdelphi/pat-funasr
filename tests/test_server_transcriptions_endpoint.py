"""
程序说明：
测试 "app/openai_api/server.py" 的 "/v1/audio/transcriptions" 参数透传行为。

目标：
- 不加载真实模型，验证新增的离线识别增强参数能从 HTTP 表单进入 generate()。
- 覆盖运行时控制项与 VAD / batch_size_s 参数，避免只有函数级测试而缺少接口级回归。
"""

import importlib.util
import os
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_OPENAI_API_DIR = _ROOT / "app" / "openai_api"
_SERVER_PATH = _OPENAI_API_DIR / "server.py"


def _load_server_module():
    sys.path.insert(0, str(_OPENAI_API_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "funasr_openai_api_server_for_transcription_tests",
            _SERVER_PATH,
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载 server 模块：{_SERVER_PATH}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if sys.path and sys.path[0] == str(_OPENAI_API_DIR):
            sys.path.pop(0)


class _DummyModel:
    def __init__(self, capture_callback):
        self._capture_callback = capture_callback

    def generate(self, **kwargs):
        self._capture_callback(kwargs)
        return [{"text": "你好，世界。", "sentence_info": [{"start": 0, "end": 800, "text": "你好，世界。"}]}]


class TestServerTranscriptionsEndpoint(unittest.TestCase):
    def setUp(self):
        self.server = _load_server_module()
        self._orig_load_model = self.server.load_model
        self._orig_ffprobe_duration_s = self.server.segmentation.ffprobe_duration_s
        self._captured_load_kwargs = None
        self._captured_generate_kwargs = None

        def dummy_load_model(model_name: str, **kwargs):
            self._captured_load_kwargs = {"model_name": model_name, **kwargs}
            return _DummyModel(capture_callback=self._capture_generate_kwargs)

        self.server.load_model = dummy_load_model
        self.server.segmentation.ffprobe_duration_s = lambda _path: 2.5

        from fastapi.testclient import TestClient

        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.server.load_model = self._orig_load_model
        self.server.segmentation.ffprobe_duration_s = self._orig_ffprobe_duration_s

    def _capture_generate_kwargs(self, kwargs):
        self._captured_generate_kwargs = kwargs

    def test_transcriptions_forward_runtime_and_vad_controls(self):
        resp = self.client.post(
            "/v1/audio/transcriptions",
            data={
                "model": "paraformer",
                "response_format": "verbose_json",
                "language": "zh",
                "hotword": "项目名,术语",
                "use_itn": "true",
                "vad_preset": "anti_hallucination",
                "vad_max_single_segment_time": "15000",
                "merge_vad": "true",
                "merge_length_s": "12",
                "batch_size_s": "30",
                "punc_mode": "disabled",
                "device": "cpu",
                "hub": "ms",
                "disable_update": "false",
                "ncpu": "2",
                "log_level": "DEBUG",
                "disable_pbar": "true",
            },
            files={"file": ("demo.wav", b"\x00\x00" * 100, "audio/wav")},
        )
        self.assertEqual(resp.status_code, 200)

        self.assertIsNotNone(self._captured_load_kwargs)
        self.assertEqual(self._captured_load_kwargs["model_name"], "paraformer")
        self.assertEqual(self._captured_load_kwargs["device"], "cpu")
        self.assertEqual(self._captured_load_kwargs["hub"], "ms")
        self.assertEqual(self._captured_load_kwargs["disable_update"], False)
        self.assertEqual(self._captured_load_kwargs["ncpu"], 2)
        self.assertEqual(self._captured_load_kwargs["log_level"], "DEBUG")
        self.assertEqual(self._captured_load_kwargs["disable_pbar"], True)
        self.assertEqual(self._captured_load_kwargs["punc_mode"], "disabled")

        self.assertIsNotNone(self._captured_generate_kwargs)
        self.assertEqual(self._captured_generate_kwargs["language"], "Chinese")
        self.assertEqual(self._captured_generate_kwargs["hotword"], "项目名,术语")
        self.assertEqual(self._captured_generate_kwargs["use_itn"], True)
        self.assertEqual(self._captured_generate_kwargs["merge_vad"], True)
        self.assertEqual(self._captured_generate_kwargs["merge_length_s"], 12)
        self.assertEqual(self._captured_generate_kwargs["batch_size_s"], 30)
        self.assertEqual(
            self._captured_generate_kwargs["vad_kwargs"]["max_single_segment_time"],
            15000,
        )

    def test_transcriptions_preserves_uploaded_audio_suffix_without_wav_conversion(self):
        resp = self.client.post(
            "/v1/audio/transcriptions",
            data={"model": "paraformer", "response_format": "json"},
            files={"file": ("demo.mp3", b"ID3" + b"\x00" * 128, "audio/mpeg")},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(self._captured_generate_kwargs)
        self.assertTrue(self._captured_generate_kwargs["input"].endswith(".mp3"))

    def test_default_model_hub_can_come_from_environment(self):
        old_value = os.environ.get("FUNASR_MODEL_HUB")
        try:
            os.environ["FUNASR_MODEL_HUB"] = "hf"
            cfg = self.server.build_model_runtime_config(
                model_name="paraformer",
                device=None,
                hub=None,
                disable_update=None,
                ncpu=None,
                log_level=None,
                disable_pbar=None,
                punc_mode=None,
            )
        finally:
            if old_value is None:
                os.environ.pop("FUNASR_MODEL_HUB", None)
            else:
                os.environ["FUNASR_MODEL_HUB"] = old_value

        self.assertEqual(cfg["hub"], "hf")

    def test_qwen_structured_timestamps_drive_verbose_json_segments(self):
        class QwenDummyModel:
            def generate(_self, **kwargs):
                self._capture_generate_kwargs(kwargs)
                return [
                    {
                        "text": "你好。欢迎！",
                        "timestamp": [[100, 300], [1200, 1500], [1520, 1800], [1820, 2100]],
                        "timestamps": [
                            {"text": "你", "start_time": 0.1, "end_time": 0.3},
                            {"text": "好", "start_time": 1.2, "end_time": 1.5},
                            {"text": "欢", "start_time": 1.52, "end_time": 1.8},
                            {"text": "迎", "start_time": 1.82, "end_time": 2.1},
                        ],
                    }
                ]

        self.server.load_model = lambda _model_name, **_kwargs: QwenDummyModel()

        resp = self.client.post(
            "/v1/audio/transcriptions",
            data={"model": "qwen3-asr", "response_format": "verbose_json"},
            files={"file": ("demo.wav", b"\x00\x00" * 100, "audio/wav")},
        )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual([seg["text"] for seg in payload["segments"]], ["你好。", "欢迎！"])
        self.assertEqual(
            [(seg["start"], seg["end"]) for seg in payload["segments"]],
            [(0.1, 1.5), (1.52, 2.1)],
        )
        self.assertTrue(self._captured_generate_kwargs["output_timestamp"])


if __name__ == "__main__":
    unittest.main()
