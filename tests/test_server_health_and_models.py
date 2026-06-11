"""
程序说明：
测试 "app/openai_api/server.py" 的 "/health" 和 "/v1/models" 端点。

目标：
- 验证 /health 返回格式正确，不暴露内部 registry key
- 验证 /v1/models 返回格式正确，包含所有已配置模型
- 验证 /v1/models/{model}/status 和 /v1/models/{model}/load 端点行为
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
            "funasr_openai_api_server_for_health_models_tests",
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


class TestHealthEndpoint(unittest.TestCase):
    def setUp(self):
        self.server = _load_server_module()
        from fastapi.testclient import TestClient
        self.client = TestClient(self.server.app)

    def test_health_returns_ok_status(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")

    def test_health_includes_device(self):
        resp = self.client.get("/health")
        data = resp.json()
        self.assertIn("device", data)

    def test_health_includes_models_lists(self):
        resp = self.client.get("/health")
        data = resp.json()
        self.assertIn("models_loaded", data)
        self.assertIn("models_available", data)
        self.assertIsInstance(data["models_loaded"], list)
        self.assertIsInstance(data["models_available"], list)

    def test_health_does_not_expose_internal_registry_keys(self):
        resp = self.client.get("/health")
        data = resp.json()
        self.assertNotIn("model_variants_loaded", data)
        self.assertNotIn("MODEL_REGISTRY", data)

    def test_health_models_available_matches_model_configs(self):
        resp = self.client.get("/health")
        data = resp.json()
        expected = sorted(self.server.MODEL_CONFIGS.keys())
        actual = sorted(data["models_available"])
        self.assertEqual(actual, expected)


class TestModelsEndpoint(unittest.TestCase):
    def setUp(self):
        self.server = _load_server_module()
        from fastapi.testclient import TestClient
        self.client = TestClient(self.server.app)

    def test_models_returns_list(self):
        resp = self.client.get("/v1/models")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("data", data)
        self.assertIsInstance(data["data"], list)

    def test_models_contains_all_configured_aliases(self):
        resp = self.client.get("/v1/models")
        data = resp.json()
        model_ids = {m["id"] for m in data["data"]}
        expected_ids = set(self.server.MODEL_CONFIGS.keys())
        self.assertEqual(model_ids, expected_ids)

    def test_models_entry_has_required_fields(self):
        resp = self.client.get("/v1/models")
        data = resp.json()
        for model in data["data"]:
            self.assertIn("id", model)
            self.assertIn("object", model)
            self.assertEqual(model["object"], "model")
            self.assertIn("created", model)
            self.assertIn("owned_by", model)
            self.assertIn("ready", model)
            self.assertIn("capabilities", model)

    def test_models_capabilities_match_model_capabilities(self):
        resp = self.client.get("/v1/models")
        data = resp.json()
        for model in data["data"]:
            model_id = model["id"]
            expected_caps = self.server.MODEL_CAPABILITIES.get(model_id, {})
            actual_caps = model["capabilities"]
            for key in ("offline_asr", "streaming_asr", "emotion"):
                self.assertEqual(
                    actual_caps.get(key),
                    expected_caps.get(key),
                    f"Model {model_id} capability {key} mismatch",
                )


class TestModelStatusAndLoad(unittest.TestCase):
    def setUp(self):
        self.server = _load_server_module()
        self._orig_load_model = self.server.load_model
        self._loaded_models = []

        def mock_load(model_name, **kwargs):
            self._loaded_models.append(model_name)
            self.server.MODEL_LOAD_STATUS[model_name] = {"state": "ready", "error": None, "updated_at": 0}
            return "dummy_model"

        self.server.load_model = mock_load
        from fastapi.testclient import TestClient
        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.server.load_model = self._orig_load_model
        self.server.MODEL_REGISTRY.clear()
        self.server.MODEL_LOAD_STATUS.clear()

    def test_model_status_not_loaded(self):
        resp = self.client.get("/v1/models/paraformer/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["model"], "paraformer")
        self.assertFalse(data["ready"])

    def test_model_load_trigger(self):
        resp = self.client.post("/v1/models/paraformer/load")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["model"], "paraformer")
        self.assertTrue(data["ready"])

    def test_model_status_unknown_returns_404(self):
        resp = self.client.get("/v1/models/nonexistent/status")
        self.assertEqual(resp.status_code, 404)

    def test_model_load_unknown_returns_404(self):
        resp = self.client.post("/v1/models/nonexistent/load")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
