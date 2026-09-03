"""
程序说明：
工作流校验、提交、状态、事件和取消端点测试。

目标：
- 不加载真实模型，验证 workflow HTTP 契约。
- 确保配置错误在提交前被拒绝。
- 确保状态和事件来自同一个任务管理器。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_OPENAI_API_DIR = _ROOT / "app" / "openai_api"
_SERVER_PATH = _OPENAI_API_DIR / "server.py"

# 固定 artifact_service._make_timestamp 输出，确保产物名可预测
_APP_DIR = _ROOT / "app"
sys.path.insert(0, str(_APP_DIR))
sys.path.insert(0, str(_OPENAI_API_DIR))
import artifact_service as _artifact_service  # noqa: E402
_artifact_service._TEST_TS = "20260903_180245"
_TS = _artifact_service._TEST_TS


def _load_server_module():
    sys.path.insert(0, str(_OPENAI_API_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "funasr_openai_api_server_for_workflow_tests",
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


class TestServerWorkflowEndpoints(unittest.TestCase):
    def setUp(self):
        self.server = _load_server_module()
        self.original_runner = self.server.WORKFLOW_RUNNER

        def runner(context):
            context.emit(
                level="progress",
                stage="transcription.primary",
                progress=0.5,
                message="测试转录",
                model="sensevoice",
            )
            return {"text": "ok", "artifacts": []}

        self.server.WORKFLOW_RUNNER = runner
        from fastapi.testclient import TestClient

        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.server.WORKFLOW_RUNNER = self.original_runner
        self.server.WORKFLOW_MANAGER.shutdown(wait=True)

    def test_validate_returns_errors_for_invalid_dependencies(self):
        resp = self.client.post(
            "/v1/funasr/workflows/validate",
            json={
                "transcription": {
                    "mode": "multi_model",
                    "primary": {"model": "sensevoice"},
                    "reviewers": [],
                }
            },
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertFalse(payload["valid"])
        self.assertIn(
            "MULTI_MODEL_REVIEWER_REQUIRED",
            {item["code"] for item in payload["errors"]},
        )

    def test_submit_status_and_events_share_one_job(self):
        workflow = {
            "transcription": {
                "mode": "single_model",
                "primary": {"model": "sensevoice"},
            }
        }
        resp = self.client.post(
            "/v1/funasr/workflows",
            data={"workflow": json.dumps(workflow, ensure_ascii=False)},
            files={"file": ("demo.wav", b"\x00\x00" * 100, "audio/wav")},
        )
        self.assertEqual(resp.status_code, 202)
        job_id = resp.json()["job_id"]

        deadline = time.monotonic() + 3
        status_payload = None
        while time.monotonic() < deadline:
            status_resp = self.client.get(f"/v1/funasr/workflows/{job_id}")
            self.assertEqual(status_resp.status_code, 200)
            status_payload = status_resp.json()
            if status_payload["status"] == "completed":
                break
            time.sleep(0.01)

        self.assertEqual(status_payload["status"], "completed")
        self.assertNotIn("path", json.dumps(status_payload.get("result") or {}))
        events_resp = self.client.get(f"/v1/funasr/workflows/{job_id}/events")
        self.assertEqual(events_resp.status_code, 200)
        events = events_resp.json()["data"]
        self.assertTrue(any(event["stage"] == "transcription.primary" for event in events))
        self.assertEqual(events[-1]["progress"], 1.0)

    def test_submit_rejects_invalid_json_before_creating_job(self):
        resp = self.client.post(
            "/v1/funasr/workflows",
            data={"workflow": "{not-json"},
            files={"file": ("demo.wav", b"\x00\x00", "audio/wav")},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("workflow JSON", resp.json()["detail"])

    def test_unknown_job_returns_404(self):
        self.assertEqual(
            self.client.get("/v1/funasr/workflows/wf_missing").status_code,
            404,
        )

    def test_runtime_status_exposes_resources_models_and_queue(self):
        response = self.client.get("/v1/runtime/status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("resources", payload)
        self.assertIn("models", payload)
        self.assertIn("workflow_queue", payload)
        self.assertIn("status_counts", payload["workflow_queue"])

    def test_default_runner_executes_fake_model_and_downloads_artifact(self):
        class FakeModel:
            def generate(self, **kwargs):
                return [
                    {
                        "text": "测试完成。",
                        "sentence_info": [
                            {"start": 0, "end": 2000, "text": "测试完成。"}
                        ],
                    }
                ]

        original_load_model = self.server.load_model
        original_probe = self.server.segmentation.ffprobe_duration_s
        self.server.WORKFLOW_RUNNER = self.server._run_workflow_job
        self.server.load_model = lambda *_args, **_kwargs: FakeModel()
        self.server.segmentation.ffprobe_duration_s = lambda _path: 2.0
        artifact_root = None
        try:
            response = self.client.post(
                "/v1/funasr/workflows",
                data={
                    "workflow": json.dumps(
                        {
                            "transcription": {"primary": {"model": "sensevoice"}},
                            "export": {"formats": ["json", "txt"]},
                        }
                    )
                },
                files={"file": ("demo.wav", b"fake-audio", "audio/wav")},
            )
            self.assertEqual(response.status_code, 202)
            job_id = response.json()["job_id"]
            snapshot = self.server.WORKFLOW_MANAGER.wait_for_terminal(job_id, timeout=3)
            self.assertEqual(snapshot["status"], "completed")
            self.assertEqual(snapshot["result"]["text"], "测试完成。")
            private_snapshot = self.server.WORKFLOW_MANAGER.get_snapshot(
                job_id,
                include_internal=True,
            )
            artifact = next(
                item for item in private_snapshot["result"]["artifacts"]
                if item["name"] == f"transcript_{_TS}.json"
            )
            artifact_root = Path(artifact["path"]).parent.parent
            download = self.client.get(
                f"/v1/funasr/workflows/{job_id}/artifacts/transcript_{_TS}.json"
            )
            self.assertEqual(download.status_code, 200)
            self.assertIn("测试完成", download.content.decode("utf-8-sig"))
        finally:
            self.server.load_model = original_load_model
            self.server.segmentation.ffprobe_duration_s = original_probe
            if artifact_root is not None:
                shutil.rmtree(artifact_root, ignore_errors=True)

    def test_workflow_temp_cleanup_removes_only_expired_directories(self):
        original_root = self.server.WORKFLOW_TEMP_ROOT
        original_ttl = self.server.WORKFLOW_TEMP_TTL_S
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            stale = root / "stale"
            fresh = root / "fresh"
            stale.mkdir()
            fresh.mkdir()
            os.utime(stale, (100.0, 100.0))
            os.utime(fresh, (950.0, 950.0))
            self.server.WORKFLOW_TEMP_ROOT = root
            self.server.WORKFLOW_TEMP_TTL_S = 100
            try:
                removed = self.server._cleanup_workflow_temp_dirs(now=1000.0)
            finally:
                self.server.WORKFLOW_TEMP_ROOT = original_root
                self.server.WORKFLOW_TEMP_TTL_S = original_ttl
            self.assertEqual(removed, 1)
            self.assertFalse(stale.exists())
            self.assertTrue(fresh.exists())


if __name__ == "__main__":
    unittest.main()
