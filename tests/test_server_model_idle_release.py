"""
程序说明：
模型闲置显存释放（5h）测试（unittest）。

目标：
- 验证 _sweep_idle_models 卸载超时模型并清理加载状态。
- 验证未过期模型不卸载、TTL=0 禁用。
- 验证 load_model 缓存命中会刷新最后使用时间。
"""

import importlib.util
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
_OPENAI_API_DIR = _ROOT / "app" / "openai_api"
_SERVER_PATH = _OPENAI_API_DIR / "server.py"
sys.path.insert(0, str(_OPENAI_API_DIR))


def _load_server_module():
    spec = importlib.util.spec_from_file_location(
        "funasr_openai_api_server_for_idle_tests",
        _SERVER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 server 模块：{_SERVER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestServerModelIdleRelease(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _load_server_module()

    def setUp(self):
        # 每个用例从干净状态开始
        self.server.MODEL_REGISTRY.clear()
        self.server._MODEL_LAST_USED.clear()
        self.server.MODEL_LOAD_STATUS.clear()

    def test_sweep_unloads_expired_model(self):
        self.server._MODEL_LAST_USED["sensevoice"] = time.time() - 3600
        self.server.MODEL_REGISTRY["sensevoice"] = object()
        self.server.MODEL_LOAD_STATUS["sensevoice"] = {
            "state": "ready",
            "error": None,
            "updated_at": time.time(),
        }
        with mock.patch("torch.cuda.is_available", return_value=False):
            unloaded = self.server._sweep_idle_models()
        self.assertEqual(unloaded, 1)
        self.assertNotIn("sensevoice", self.server.MODEL_REGISTRY)
        self.assertNotIn("sensevoice", self.server._MODEL_LAST_USED)
        # 加载状态一并清理，前端不再误报 ready
        self.assertNotIn("sensevoice", self.server.MODEL_LOAD_STATUS)

    def test_sweep_keeps_recent_model(self):
        self.server._MODEL_LAST_USED["sensevoice"] = time.time()
        self.server.MODEL_REGISTRY["sensevoice"] = object()
        unloaded = self.server._sweep_idle_models()
        self.assertEqual(unloaded, 0)
        self.assertIn("sensevoice", self.server.MODEL_REGISTRY)

    def test_sweep_disabled_when_ttl_zero(self):
        self.server._MODEL_LAST_USED["sensevoice"] = time.time() - 3600
        self.server.MODEL_REGISTRY["sensevoice"] = object()
        original_ttl = self.server._MODEL_IDLE_TTL_S
        self.server._MODEL_IDLE_TTL_S = 0
        try:
            unloaded = self.server._sweep_idle_models()
        finally:
            self.server._MODEL_IDLE_TTL_S = original_ttl
        self.assertEqual(unloaded, 0)
        self.assertIn("sensevoice", self.server.MODEL_REGISTRY)

    def test_load_model_cache_hit_refreshes_last_used(self):
        # 与 load_model 内部相同的默认参数，保证 registry_key 一致
        cfg = self.server.build_model_runtime_config(
            model_name="sensevoice",
            device=None,
            hub=None,
            disable_update=None,
            ncpu=None,
            log_level=None,
            disable_pbar=None,
            punc_mode=None,
        )
        key = self.server.build_model_registry_key("sensevoice", cfg)
        fake_model = object()
        self.server.MODEL_REGISTRY[key] = fake_model
        self.server._MODEL_LAST_USED[key] = time.time() - 9999
        model = self.server.load_model("sensevoice")
        self.assertIs(model, fake_model)
        # 缓存命中即刷新最后使用时间，防止误卸载
        self.assertGreater(self.server._MODEL_LAST_USED[key], time.time() - 5)


if __name__ == "__main__":
    unittest.main()
