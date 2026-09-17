"""
程序说明：
测试 LLM 熔断状态只读端点（5b）与快照内容。

目标：
- 不加载真实模型，验证 GET /v1/funasr/llm/fuse-state 契约。
- 验证与真实调用链共享 openai_api.llm_client 的同一模块实例。
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
            "funasr_openai_api_server_for_fuse_state_tests",
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


class TestLlmFuseStateEndpoint(unittest.TestCase):
    def setUp(self):
        self.server = _load_server_module()
        from openai_api import llm_client

        self.llm_client = llm_client
        # 清空熔断状态，保证用例独立
        with llm_client._fuse_lock:
            llm_client._fuse_state.clear()

        from fastapi.testclient import TestClient

        self.client = TestClient(self.server.app)

    def tearDown(self):
        with self.llm_client._fuse_lock:
            self.llm_client._fuse_state.clear()

    def test_empty_snapshot(self):
        resp = self.client.get("/v1/funasr/llm/fuse-state")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"fuse_state": {}})

    def test_snapshot_shows_active_fuse(self):
        # 直接走内部路径制造一条熔断记录（连失败 2 次触发熔断）
        key = ("http://llm.example.com/v1", "qwen3.7-plus")
        for reason in ("HTTP 500: boom", "HTTP 429: busy"):
            self.llm_client._mark_fail(key, reason)

        resp = self.client.get("/v1/funasr/llm/fuse-state")
        self.assertEqual(resp.status_code, 200)
        fuse_state = resp.json()["fuse_state"]
        self.assertEqual(len(fuse_state), 1)
        entry = fuse_state["http://llm.example.com/v1|qwen3.7-plus"]
        self.assertEqual(entry["base_url"], "http://llm.example.com/v1")
        self.assertEqual(entry["model"], "qwen3.7-plus")
        self.assertEqual(entry["fail_streak"], 2)
        self.assertTrue(entry["active"])
        self.assertGreater(entry["remaining_seconds"], 0)
        self.assertEqual(entry["last_reason"], "HTTP 429: busy")

    def test_mark_ok_resets_snapshot(self):
        key = ("http://llm.example.com/v1", "qwen3.7-plus")
        self.llm_client._mark_fail(key, "HTTP 500: boom")
        self.llm_client._mark_fail(key, "HTTP 500: boom")
        self.llm_client._mark_ok(key)
        resp = self.client.get("/v1/funasr/llm/fuse-state")
        entry = resp.json()["fuse_state"]["http://llm.example.com/v1|qwen3.7-plus"]
        self.assertEqual(entry["fail_streak"], 0)
        self.assertFalse(entry["active"])


if __name__ == "__main__":
    unittest.main()
