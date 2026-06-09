"""
程序说明：
测试 "app/openai_api/server.py" 的 "/v1/funasr/diarization" 最小协议。

目标：
- 不加载真实模型（用 dummy model 替换 load_model），验证路由参数校验与成功返回结构。
- 覆盖：模型不支持、spk_mode 非法、成功路径（segments/speakers/text）。
"""

import importlib.util
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
            "funasr_openai_api_server_for_diarization_tests",
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
    def __init__(self, on_generate):
        self._on_generate = on_generate

    def generate(self, **kwargs):
        return self._on_generate(kwargs)


class TestServerDiarizationEndpoint(unittest.TestCase):
    def setUp(self):
        self.server = _load_server_module()
        self._orig_load_model = self.server.load_model
        self.captured_load_kwargs = None
        self.captured_generate_kwargs = None

        def dummy_load_model(_model_name: str, **kwargs):
            self.captured_load_kwargs = kwargs
            def on_generate(kwargs):
                self.captured_generate_kwargs = kwargs
                return [
                    {
                        "text": "你好 欢迎光临",
                        "sentence_info": [
                            {"start": 0, "end": 1200, "text": "你好", "spk": 0},
                            {"start": 1200, "end": 2800, "text": "欢迎光临", "spk": 1},
                        ],
                    }
                ]

            return _DummyModel(on_generate=on_generate)

        self.server.load_model = dummy_load_model

        from fastapi.testclient import TestClient

        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.server.load_model = self._orig_load_model

    def test_diarization_rejects_non_supported_model(self):
        resp = self.client.post(
            "/v1/funasr/diarization",
            data={"model": "qwen3-asr", "spk_model": "cam++", "spk_mode": "punc_segment"},
            files={"file": ("demo.wav", b"\x00\x00", "audio/wav")},
        )
        self.assertEqual(resp.status_code, 400)

    def test_diarization_rejects_invalid_spk_mode(self):
        resp = self.client.post(
            "/v1/funasr/diarization",
            data={"model": "paraformer", "spk_model": "cam++", "spk_mode": "segment"},
            files={"file": ("demo.wav", b"\x00\x00", "audio/wav")},
        )
        self.assertEqual(resp.status_code, 400)

    def test_diarization_success_returns_segments_and_speakers(self):
        resp = self.client.post(
            "/v1/funasr/diarization",
            data={
                "model": "paraformer",
                "spk_model": "cam++",
                "spk_mode": "punc_segment",
                "preset_spk_num": "2",
            },
            files={"file": ("demo.wav", b"\x00\x00" * 100, "audio/wav")},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["model"], "paraformer")
        self.assertEqual(payload["spk_model"], "cam++")
        self.assertEqual(payload["spk_mode"], "punc_segment")
        self.assertEqual(payload["speakers"], [0, 1])
        self.assertEqual(payload["segments"][0]["speaker"], 0)
        self.assertEqual(payload["segments"][1]["speaker"], 1)
        self.assertEqual(payload["text"], "你好 欢迎光临")
        self.assertIsNotNone(self.captured_load_kwargs)
        self.assertEqual(self.captured_load_kwargs.get("spk_model"), "cam++")
        self.assertEqual(self.captured_generate_kwargs.get("output_timestamp"), True)

    def test_sensevoice_diarization_falls_back_to_vad_segment(self):
        resp = self.client.post(
            "/v1/funasr/diarization",
            data={
                "model": "sensevoice",
                "spk_model": "cam++",
                "spk_mode": "punc_segment",
            },
            files={"file": ("demo.wav", b"\x00\x00" * 100, "audio/wav")},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["spk_mode"], "vad_segment")
        self.assertEqual(self.captured_load_kwargs.get("punc_mode"), "disabled")
        self.assertEqual(self.captured_generate_kwargs.get("spk_mode"), "vad_segment")

    def test_fun_asr_nano_sentence_field_is_converted_to_segments(self):
        def dummy_load_model(_model_name: str, **kwargs):
            self.captured_load_kwargs = kwargs

            def on_generate(kwargs):
                self.captured_generate_kwargs = kwargs
                return [
                    {
                        "text": "hello world",
                        "sentence_info": [
                            {
                                "start": 0,
                                "end": 1500,
                                "sentence": "hello",
                                "spk": 0,
                            },
                            {
                                "start": 1500,
                                "end": 3000,
                                "sentence": "world",
                                "spk": 1,
                            },
                        ],
                    }
                ]

            return _DummyModel(on_generate=on_generate)

        self.server.load_model = dummy_load_model
        resp = self.client.post(
            "/v1/funasr/diarization",
            data={
                "model": "fun-asr-nano",
                "spk_model": "cam++",
                "spk_mode": "punc_segment",
            },
            files={"file": ("demo.wav", b"\x00\x00" * 100, "audio/wav")},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["segments"][0]["text"], "hello")
        self.assertEqual(payload["segments"][0]["speaker"], 0)
        self.assertEqual(payload["segments"][1]["text"], "world")
        self.assertEqual(payload["segments"][1]["speaker"], 1)
        self.assertEqual(payload["speakers"], [0, 1])


if __name__ == "__main__":
    unittest.main()
