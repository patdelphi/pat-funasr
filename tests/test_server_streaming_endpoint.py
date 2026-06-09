"""
程序说明：
测试 "app/openai_api/server.py" 的 "/v1/funasr/streaming" 最小协议。

目标：
- 不加载真实模型（用 dummy model 替换 load_model），验证路由参数校验与 session/cache 机制。
- 覆盖：模型不支持、chunk_size 非法、成功路径（cache 复用 + full_text 叠加）。
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
            "funasr_openai_api_server_for_streaming_tests",
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


class TestServerStreamingEndpoint(unittest.TestCase):
    def setUp(self):
        self.server = _load_server_module()
        self._orig_load_model = self.server.load_model
        self._orig_model_configs = dict(self.server.MODEL_CONFIGS)
        self.captured_load_kwargs = None

        self.server.MODEL_CONFIGS["paraformer-zh-streaming"] = {
            "model": "paraformer-zh-streaming",
            "hub": "ms",
            "punc_model": "ct-punc",
        }

        def dummy_load_model(_model_name: str, **kwargs):
            self.captured_load_kwargs = kwargs
            def on_generate(kwargs):
                cache = kwargs.get("cache")
                if isinstance(cache, dict):
                    cache["touched"] = cache.get("touched", 0) + 1
                    touched = cache["touched"]
                else:
                    touched = 1
                return [{"text": "hi" if touched == 1 else "hithere"}]

            return _DummyModel(on_generate=on_generate)

        self.server.load_model = dummy_load_model

        try:
            self.server.STREAMING_SESSIONS.clear()
        except Exception:
            pass

        from fastapi.testclient import TestClient

        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.server.load_model = self._orig_load_model
        self.server.MODEL_CONFIGS.clear()
        self.server.MODEL_CONFIGS.update(self._orig_model_configs)
        try:
            self.server.STREAMING_SESSIONS.clear()
        except Exception:
            pass

    def test_streaming_rejects_non_streaming_model(self):
        resp = self.client.post(
            "/v1/funasr/streaming",
            data={"model": "sensevoice", "session_id": "s1", "is_final": "false"},
            files={"file": ("chunk.pcm", b"\x00\x00", "application/octet-stream")},
        )
        self.assertEqual(resp.status_code, 400)

    def test_streaming_rejects_invalid_chunk_size(self):
        resp = self.client.post(
            "/v1/funasr/streaming",
            data={
                "model": "paraformer-zh-streaming",
                "session_id": "s1",
                "chunk_size": "0,10",
                "is_final": "false",
            },
            files={"file": ("chunk.pcm", b"\x00\x00", "application/octet-stream")},
        )
        self.assertEqual(resp.status_code, 400)

    def test_streaming_session_cache_and_full_text(self):
        r1 = self.client.post(
            "/v1/funasr/streaming",
            data={"model": "paraformer-zh-streaming", "session_id": "s1", "is_final": "false"},
            files={"file": ("chunk.pcm", b"\x00\x00" * 100, "application/octet-stream")},
        )
        self.assertEqual(r1.status_code, 200)
        p1 = r1.json()
        self.assertEqual(p1["session_id"], "s1")
        self.assertEqual(p1["text"], "hi")
        self.assertEqual(p1["full_text"], "hi")
        self.assertEqual(self.captured_load_kwargs or {}, {})
        self.assertEqual(self.server.MODEL_CONFIGS["paraformer-zh-streaming"].get("punc_model"), "ct-punc")

        r2 = self.client.post(
            "/v1/funasr/streaming",
            data={"model": "paraformer-zh-streaming", "session_id": "s1", "is_final": "true"},
            files={"file": ("chunk.pcm", b"\x00\x00" * 80, "application/octet-stream")},
        )
        self.assertEqual(r2.status_code, 200)
        p2 = r2.json()
        self.assertEqual(p2["session_id"], "s1")
        self.assertEqual(p2["text"], "hithere")
        self.assertEqual(p2["full_text"], "hithere")

    def test_merge_streaming_text_supports_delta_and_cumulative_text(self):
        self.assertEqual(self.server.merge_streaming_text("", "你好"), "你好")
        self.assertEqual(self.server.merge_streaming_text("你好", "世界"), "你好世界")
        self.assertEqual(self.server.merge_streaming_text("你好", "你好啊"), "你好啊")
        self.assertEqual(self.server.merge_streaming_text("你好啊", "啊"), "你好啊")


if __name__ == "__main__":
    unittest.main()
