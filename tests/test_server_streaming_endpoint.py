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

import numpy as np


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
        self.captured_generate_kwargs = None

        self.server.MODEL_CONFIGS["paraformer-zh-streaming"] = {
            "model": "paraformer-zh-streaming",
            "hub": "ms",
            "punc_model": "ct-punc",
        }

        def dummy_load_model(_model_name: str, **kwargs):
            self.captured_load_kwargs = kwargs
            def on_generate(kwargs):
                self.captured_generate_kwargs = kwargs
                cache = kwargs.get("cache")
                if isinstance(cache, dict):
                    cache["touched"] = cache.get("touched", 0) + 1
                    touched = cache["touched"]
                else:
                    touched = 1
                return [{"text": "hi" if touched == 1 else "hithere"}]

            model = _DummyModel(on_generate=on_generate)
            self.server.MODEL_REGISTRY[_model_name] = model
            self.server.MODEL_LOAD_STATUS[_model_name] = {
                "state": "ready",
                "error": None,
                "updated_at": 1.0,
            }
            return model

        self.server.load_model = dummy_load_model

        try:
            self.server.STREAMING_SESSIONS.clear()
            self.server.MODEL_REGISTRY.clear()
            self.server.MODEL_LOAD_STATUS.clear()
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
            self.server.MODEL_REGISTRY.clear()
            self.server.MODEL_LOAD_STATUS.clear()
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
        self.assertIsNotNone(self.captured_generate_kwargs)
        audio_input = self.captured_generate_kwargs["input"]
        self.assertIsInstance(audio_input, np.ndarray)
        self.assertEqual(audio_input.dtype, np.float32)

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

    def test_pcm16_bytes_to_float32_audio_scales_samples(self):
        pcm = np.array([0, 16384, -32768], dtype=np.int16).tobytes()
        audio = self.server.pcm16_bytes_to_float32_audio(pcm)

        self.assertEqual(audio.dtype, np.float32)
        np.testing.assert_allclose(audio, np.array([0.0, 0.5, -1.0], dtype=np.float32))

    def test_model_status_and_preload_endpoints(self):
        status = self.client.get("/v1/models/paraformer-zh-streaming/status")
        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.json()["ready"])
        self.assertEqual(status.json()["state"], "not_loaded")

        loaded = self.client.post("/v1/models/paraformer-zh-streaming/load")
        self.assertEqual(loaded.status_code, 200)
        self.assertTrue(loaded.json()["ready"])
        self.assertEqual(loaded.json()["state"], "ready")

        status_after = self.client.get("/v1/models/paraformer-zh-streaming/status")
        self.assertEqual(status_after.status_code, 200)
        self.assertTrue(status_after.json()["ready"])

    def test_native_mic_stream_page_contains_capture_and_streaming_code(self):
        html = self.server.build_native_mic_stream_html()

        self.assertIn("navigator.mediaDevices.getUserMedia", html)
        self.assertIn("AudioContext", html)
        self.assertIn("createScriptProcessor", html)
        self.assertIn("/v1/funasr/streaming", html)
        self.assertIn("encoder_chunk_look_back", html)
        self.assertIn('value="0,30,15"', html)
        self.assertIn("currentChunkSamples", html)
        self.assertIn("deviceSelect.onchange", html)
        self.assertIn('<select id="deviceSelect"><option value="">系统默认输入设备</option></select>', html)
        self.assertIn("持续近静音", html)
        self.assertIn("resetDeviceOptions", html)
        self.assertIn("当前浏览器不支持 mediaDevices.enumerateDevices。", html)
        self.assertIn("microphonePermissionState", html)
        self.assertIn("麦克风权限被浏览器拒绝", html)
        self.assertIn("麦克风权限被拒绝，无法读取真实设备名", html)
        self.assertNotIn("probeMicrophonePermission", html)
        self.assertNotIn("requestPermissionWhenEmpty", html)
        self.assertIn("系统默认输入设备", html)
        self.assertIn("defaultInput || inputs[0]", html)
        self.assertIn("启动后没有收到音频回调", html)
        self.assertIn("下载录音", html)
        self.assertIn("下载识别结果", html)
        self.assertIn("downloadTranscriptLink", html)
        self.assertIn('text.replace(/\\n/g, "\\r\\n")', html)
        self.assertIn("let flushPromise = Promise.resolve()", html)
        self.assertIn("function queueFlush", html)
        self.assertIn('hasOwnProperty.call(payload, "full_text")', html)
        self.assertIn("后端暂未返回文字", html)
        self.assertIn("/v1/models/${model}/status", html)
        self.assertIn("/v1/models/${encodeURIComponent(modelName)}/load", html)
        self.assertIn("ensureModelReadyBeforeRecording", html)
        self.assertIn("加载中，请稍候", html)
        self.assertIn('class="primary" disabled', html)
        self.assertIn("color-scheme: light dark", html)
        self.assertIn("prefers-color-scheme: dark", html)
        self.assertIn("function applyTheme", html)
        self.assertIn('type === "pat-theme"', html)
        self.assertIn('theme === "light"', html)
        self.assertIn('theme === "dark"', html)
        self.assertIn("--primary: #2563eb", html)
        self.assertIn(".stats { display: flex", html)
        self.assertNotIn("height: 100vh", html)
        self.assertIn(".download-row { margin-top: auto", html)
        self.assertIn("section { background: var(--panel); border: 0", html)

    def test_native_mic_stream_route_returns_html(self):
        resp = self.client.get("/mic-stream")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))
        self.assertIn("Mic 实时识别", resp.text)


if __name__ == "__main__":
    unittest.main()
