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

    def test_refresh_events_artifact_writes_terminal_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            events_path = Path(tmpdir) / "events.jsonl"
            events_path.write_text("old", encoding="utf-8")
            snapshot = {
                "events": [
                    {"event_id": 1, "message": "正在导出"},
                    {"event_id": 2, "message": "导出完成"},
                    {"event_id": 3, "message": "任务完成"},
                ],
                "result": {
                    "artifacts": [
                        {"name": "events.jsonl", "path": str(events_path)}
                    ]
                },
            }

            artifact_service.refresh_events_artifact(snapshot)

            lines = events_path.read_text(encoding="utf-8-sig").splitlines()
            self.assertEqual(len(lines), 3)
            self.assertEqual(json.loads(lines[-1])["message"], "任务完成")

    def test_txt_uses_final_text_when_whole_text_proofread_changed_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_service.write_workflow_artifacts(
                output_dir=tmpdir,
                result={
                    "text": "校对后的最终文本",
                    "refined_text": "校对后的最终文本",
                    "segments": [{"text": "原始文本", "start": 0, "end": 1}],
                },
                config={},
                events=[],
                formats=["txt"],
                include_raw_candidates=False,
                include_config_snapshot=False,
            )

            output = (Path(tmpdir) / "transcript.txt").read_text(encoding="utf-8-sig")

        self.assertEqual(output.strip(), "校对后的最终文本")


if __name__ == "__main__":
    unittest.main()
