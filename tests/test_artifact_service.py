"""
程序说明：
验证精细转录工作流产物统一导出，确保格式、配置快照与事件日志可审计。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_OPENAI_API_DIR = _ROOT / "app" / "openai_api"
if str(_OPENAI_API_DIR) not in sys.path:
    sys.path.insert(0, str(_OPENAI_API_DIR))

import artifact_service  # noqa: E402


class TestArtifactService(unittest.TestCase):
    def test_writes_selected_formats_and_audit_files(self):
        result = {
            "text": "你好世界",
            "segments": [
                {"start": 0.0, "end": 1.2, "text": "你好", "speaker": "S1"},
                {"start": 1.2, "end": 2.4, "text": "世界", "speaker": "S2"},
            ],
            "model_runs": [{"model": "sensevoice", "text": "你好世界"}],
        }
        config = {"workflow_version": "1.0", "export": {"formats": ["json", "srt"]}}
        events = [{"event_id": 1, "stage": "queue", "message": "任务已进入队列"}]

        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts = artifact_service.write_workflow_artifacts(
                output_dir=tmpdir,
                result=result,
                config=config,
                events=events,
                formats=["json", "srt"],
                include_raw_candidates=False,
                include_config_snapshot=True,
            )
            names = {item["name"] for item in artifacts}
            self.assertEqual(
                names,
                {"transcript.json", "transcript.srt", "workflow-config.json", "events.jsonl"},
            )

            json_path = Path(tmpdir) / "transcript.json"
            raw = json_path.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
            payload = json.loads(raw.decode("utf-8-sig"))
            self.assertNotIn("model_runs", payload)
            self.assertEqual(payload["segments"][0]["speaker"], "S1")

    def test_all_expands_to_five_transcript_formats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts = artifact_service.write_workflow_artifacts(
                output_dir=tmpdir,
                result={"text": "A", "segments": [{"start": 0, "end": 1, "text": "A"}]},
                config={},
                events=[],
                formats=["all"],
                include_raw_candidates=True,
                include_config_snapshot=False,
            )
            names = {item["name"] for item in artifacts}
            self.assertTrue(
                {"transcript.json", "transcript.txt", "transcript.srt", "transcript.vtt", "transcript.tsv"}
                <= names
            )
            self.assertIn("events.jsonl", names)


if __name__ == "__main__":
    unittest.main()
