"""
程序说明：
测试 server.py 的热词热更新 API（5e）与生效规则。

目标：
- 不加载真实模型，验证 GET/PUT/DELETE /v1/funasr/hotwords 契约。
- 验证 _effective_hotword 规则：显式热词 > 运行时覆盖 > 无。
- 验证 /v1/audio/transcriptions 无显式热词时应用运行时覆盖。
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
            "funasr_openai_api_server_for_hotword_tests",
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


class TestServerHotwordApi(unittest.TestCase):
    def setUp(self):
        self.server = _load_server_module()
        # 每个用例从干净状态开始
        with self.server._HOTWORD_LOCK:
            self.server._HOTWORD_OVERRIDES.clear()

        self._orig_load_model = self.server.load_model
        self._orig_ffprobe_duration_s = self.server.segmentation.ffprobe_duration_s
        self._captured_generate_kwargs = None

        def dummy_load_model(model_name: str, **kwargs):
            return _DummyModel(capture_callback=self._capture_generate_kwargs)

        self.server.load_model = dummy_load_model
        self.server.segmentation.ffprobe_duration_s = lambda _path: 2.5

        from fastapi.testclient import TestClient

        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.server.load_model = self._orig_load_model
        self.server.segmentation.ffprobe_duration_s = self._orig_ffprobe_duration_s
        with self.server._HOTWORD_LOCK:
            self.server._HOTWORD_OVERRIDES.clear()

    def _capture_generate_kwargs(self, kwargs):
        self._captured_generate_kwargs = kwargs

    def _transcribe(self, *, hotword=None):
        data = {"model": "paraformer", "response_format": "verbose_json"}
        if hotword is not None:
            data["hotword"] = hotword
        resp = self.client.post(
            "/v1/audio/transcriptions",
            data=data,
            files={"file": ("demo.wav", b"\x00\x00" * 100, "audio/wav")},
        )
        self.assertEqual(resp.status_code, 200)
        return resp

    def test_hotword_crud_endpoints(self):
        # 初始为空
        resp = self.client.get("/v1/funasr/hotwords")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"hotwords": {}})

        # PUT 设置
        resp = self.client.put(
            "/v1/funasr/hotwords/paraformer",
            json={"hotword": "项目名,术语"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"model": "paraformer", "hotword": "项目名,术语"})

        resp = self.client.get("/v1/funasr/hotwords")
        self.assertEqual(resp.json()["hotwords"], {"paraformer": "项目名,术语"})

        # PUT 空串 = 清除
        resp = self.client.put("/v1/funasr/hotwords/paraformer", json={"hotword": "  "})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"model": "paraformer", "hotword": ""})
        resp = self.client.get("/v1/funasr/hotwords")
        self.assertEqual(resp.json()["hotwords"], {})

        # DELETE 清除
        self.client.put("/v1/funasr/hotwords/sensevoice", json={"hotword": "语音助手"})
        resp = self.client.delete("/v1/funasr/hotwords/sensevoice")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"model": "sensevoice", "removed": True})
        # 再删一次：removed=False
        resp = self.client.delete("/v1/funasr/hotwords/sensevoice")
        self.assertEqual(resp.json()["removed"], False)

        # 空 model 拒绝
        resp = self.client.put("/v1/funasr/hotwords/   ", json={"hotword": "x"})
        self.assertEqual(resp.status_code, 400)

    def test_effective_hotword_priority(self):
        """显式热词 > 运行时覆盖 > 无。"""
        self.server._HOTWORD_OVERRIDES["paraformer"] = "覆盖词"
        # 显式优先
        self.assertEqual(self.server._effective_hotword("paraformer", "显式词"), "显式词")
        self.assertEqual(self.server._effective_hotword("paraformer", ""), None)
        # 无显式时用覆盖
        self.assertEqual(self.server._effective_hotword("paraformer", None), "覆盖词")
        # 无覆盖返回 None
        self.assertEqual(self.server._effective_hotword("sensevoice", None), None)

    def test_transcription_applies_override_without_explicit_hotword(self):
        self.server._HOTWORD_OVERRIDES["paraformer"] = "覆盖热词"
        self._transcribe()
        self.assertEqual(self._captured_generate_kwargs["hotword"], "覆盖热词")

    def test_transcription_explicit_hotword_wins(self):
        self.server._HOTWORD_OVERRIDES["paraformer"] = "覆盖热词"
        self._transcribe(hotword="显式热词")
        self.assertEqual(self._captured_generate_kwargs["hotword"], "显式热词")


if __name__ == "__main__":
    unittest.main()
