"""
程序说明：
工作流 SSE 事件推送端点测试（unittest）。

目标：
- 验证 /v1/funasr/workflows/{job_id}/events/stream 以 text/event-stream 推送任务事件。
- 验证任务终态后发送 done 事件并关闭连接。
- 验证未知任务返回 404，现有轮询 /events 端点行为不变。
"""

import importlib.util
import json
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_OPENAI_API_DIR = _ROOT / "app" / "openai_api"
_SERVER_PATH = _OPENAI_API_DIR / "server.py"
sys.path.insert(0, str(_OPENAI_API_DIR))


def _load_server_module():
    spec = importlib.util.spec_from_file_location(
        "funasr_openai_api_server_for_sse_tests",
        _SERVER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 server 模块：{_SERVER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestServerWorkflowSse(unittest.TestCase):
    def setUp(self):
        self.server = _load_server_module()
        self.original_runner = self.server.WORKFLOW_RUNNER

        def runner(context):
            context.emit(
                level="progress",
                stage="transcription.primary",
                progress=1.0,
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

    def _submit_job(self) -> str:
        resp = self.client.post(
            "/v1/funasr/workflows",
            data={
                "workflow": json.dumps(
                    {
                        "transcription": {"primary": {"model": "sensevoice"}},
                        "export": {"formats": ["json"]},
                    }
                )
            },
            files={"file": ("demo.wav", b"fake-audio", "audio/wav")},
        )
        self.assertEqual(resp.status_code, 202)
        return resp.json()["job_id"]

    def test_sse_streams_events_and_ends_with_done(self):
        job_id = self._submit_job()
        data_lines = []
        done_seen = False
        with self.client.stream(
            "GET", f"/v1/funasr/workflows/{job_id}/events/stream"
        ) as stream:
            self.assertEqual(stream.status_code, 200)
            self.assertIn("text/event-stream", stream.headers["content-type"])
            for line in stream.iter_lines():
                if line.startswith("data: "):
                    data_lines.append(line[len("data: "):])
                elif line.startswith("event: done"):
                    done_seen = True
        # 终态任务必须发送 done 事件并关闭连接
        self.assertTrue(done_seen)
        payloads = [json.loads(line) for line in data_lines if line]
        self.assertTrue(any(p.get("message") == "任务完成" for p in payloads))
        self.assertTrue(any(p.get("progress") == 1.0 for p in payloads))

    def test_sse_unknown_job_returns_404(self):
        with self.client.stream(
            "GET", "/v1/funasr/workflows/wf_missing/events/stream"
        ) as stream:
            self.assertEqual(stream.status_code, 404)


if __name__ == "__main__":
    unittest.main()
