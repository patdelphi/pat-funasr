"""
程序说明：
测试 "app/openai_api/server.py" 的 "/v1/funasr/emotion" 最小协议。

目标：
- 不加载真实模型（用 dummy model 替换 load_model），验证路由参数校验与成功返回结构。
- 覆盖：模型不支持、granularity 非法、成功路径（返回 top_emotion / emotions 列表）。
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
            "funasr_openai_api_server_for_emotion_tests",
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


class TestServerEmotionEndpoint(unittest.TestCase):
    def setUp(self):
        self.server = _load_server_module()
        self._orig_load_model = self.server.load_model
        self._orig_model_configs = dict(self.server.MODEL_CONFIGS)
        self._orig_capabilities = dict(self.server.MODEL_CAPABILITIES)

        self.server.MODEL_CONFIGS["emotion2vec-plus-large"] = {
            "model": "iic/emotion2vec_plus_large",
            "hub": "ms",
        }
        self.server.MODEL_CAPABILITIES["emotion2vec-plus-large"] = {
            "offline_asr": False,
            "streaming_asr": False,
            "diarization": False,
            "emotion": True,
            "vad": False,
            "punc": False,
            "notes": "独立情感识别模型",
        }

        def dummy_load_model(_model_name: str):
            def on_generate(kwargs):
                if _model_name == "sensevoice":
                    return [
                        {
                            "key": "sample",
                            "text": "<|zh|><|HAPPY|><|Speech|><|withitn|>今天真是太开心了。",
                        }
                    ]
                return [
                    {
                        "key": "sample",
                        "labels": ["neutral", "happy", "sad"],
                        "scores": [0.2, 0.7, 0.1],
                    }
                ]

            return _DummyModel(on_generate=on_generate)

        self.server.load_model = dummy_load_model

        from fastapi.testclient import TestClient

        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.server.load_model = self._orig_load_model
        self.server.MODEL_CONFIGS.clear()
        self.server.MODEL_CONFIGS.update(self._orig_model_configs)
        self.server.MODEL_CAPABILITIES.clear()
        self.server.MODEL_CAPABILITIES.update(self._orig_capabilities)

    def test_emotion_rejects_non_emotion_model(self):
        resp = self.client.post(
            "/v1/funasr/emotion",
            data={"model": "qwen3-asr", "granularity": "utterance"},
            files={"file": ("demo.wav", b"\x00\x00", "audio/wav")},
        )
        self.assertEqual(resp.status_code, 400)

    def test_emotion_rejects_invalid_granularity(self):
        resp = self.client.post(
            "/v1/funasr/emotion",
            data={"model": "emotion2vec-plus-large", "granularity": "segment"},
            files={"file": ("demo.wav", b"\x00\x00", "audio/wav")},
        )
        self.assertEqual(resp.status_code, 400)

    def test_emotion_success_returns_top_emotion(self):
        resp = self.client.post(
            "/v1/funasr/emotion",
            data={"model": "emotion2vec-plus-large", "granularity": "utterance"},
            files={"file": ("demo.wav", b"\x00\x00" * 100, "audio/wav")},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["model"], "emotion2vec-plus-large")
        self.assertEqual(payload["top_emotion"], "happy")
        self.assertAlmostEqual(payload["top_score"], 0.7, places=6)
        self.assertEqual(payload["emotions"][0]["label"], "happy")
        self.assertEqual(payload["emotions"][1]["label"], "neutral")
        self.assertEqual(payload["granularity"], "utterance")

    def test_emotion_supports_sensevoice_utterance(self):
        resp = self.client.post(
            "/v1/funasr/emotion",
            data={"model": "sensevoice", "granularity": "utterance"},
            files={"file": ("demo.wav", b"\x00\x00" * 100, "audio/wav")},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["model"], "sensevoice")
        self.assertEqual(payload["top_emotion"], "happy")
        self.assertEqual(payload["granularity"], "utterance")
        self.assertIn("今天真是太开心了。", payload["text"])

    def test_emotion_supports_sensevoice_spaced_tokens(self):
        def dummy_load_model(_model_name: str):
            return _DummyModel(
                on_generate=lambda _kwargs: [
                    {
                        "key": "sample",
                        "text": "< | zh | > < | HAPPY | > < | BGM | > < | wo itn | >今天真是太开心了。"
                                "< | zh | > < | EMO _ UNKNOWN | > < | Speech | > < | wo itn | >继续说话。",
                    }
                ]
            )

        self.server.load_model = dummy_load_model
        resp = self.client.post(
            "/v1/funasr/emotion",
            data={"model": "sensevoice", "granularity": "utterance"},
            files={"file": ("demo.wav", b"\x00\x00" * 100, "audio/wav")},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["top_emotion"], "happy")
        self.assertEqual(payload["emotions"], [{"label": "happy", "score": 1.0}])

    def test_emotion_rejects_sensevoice_frame(self):
        resp = self.client.post(
            "/v1/funasr/emotion",
            data={"model": "sensevoice", "granularity": "frame"},
            files={"file": ("demo.wav", b"\x00\x00" * 100, "audio/wav")},
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
