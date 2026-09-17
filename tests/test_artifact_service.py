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
            # JSON 文件不加 BOM（只有 Excel 友好格式 txt/tsv/srt/vtt 加 BOM）
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
            payload = json.loads(raw.decode("utf-8"))
            self.assertNotIn("model_runs", payload)
            self.assertEqual(payload["segments"][0]["speaker"], "S1")

            # SRT 应该有 BOM（Excel 友好）
            srt_path = Path(tmpdir) / "transcript.srt"
            srt_raw = srt_path.read_bytes()
            self.assertTrue(srt_raw.startswith(b"\xef\xbb\xbf"))

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

    def test_writes_csv_and_docx_formats(self):
        """显式选择 csv/docx 时应写出对应产物；'all' 不包含它们（保持原 5 件套）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts = artifact_service.write_workflow_artifacts(
                output_dir=tmpdir,
                result={
                    "text": "你好",
                    "segments": [
                        {"start": 0.0, "end": 1.2, "text": "你好", "speaker": "S1"},
                    ],
                },
                config={},
                events=[],
                formats=["csv", "docx"],
                include_raw_candidates=False,
                include_config_snapshot=False,
            )
            names = {item["name"] for item in artifacts}
            self.assertIn("transcript.csv", names)
            self.assertIn("transcript.docx", names)

            csv_text = (Path(tmpdir) / "transcript.csv").read_text(encoding="utf-8-sig")
            self.assertEqual(csv_text.splitlines()[0], "start,end,speaker,text")
            self.assertIn("S1", csv_text)

            # DOCX 是合法 ZIP 且含 document.xml
            import zipfile
            with zipfile.ZipFile(Path(tmpdir) / "transcript.docx") as zf:
                self.assertIn("word/document.xml", zf.namelist())

        # 'all' 仍然只展开为基础 5 件套，不引入 csv/docx
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
            self.assertNotIn("transcript.csv", names)
            self.assertNotIn("transcript.docx", names)

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

    def test_txt_uses_segments_rendered_for_speaker_tags(self):
        """TXT 应该用 render_txt(segments) 保留说话人标签和段落空行，而非 refined_text 纯文本。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_service.write_workflow_artifacts(
                output_dir=tmpdir,
                result={
                    "text": "校对后的最终文本",
                    "refined_text": "校对后的最终文本",
                    "segments": [
                        {"text": "你好世界", "start": 0, "end": 1, "speaker": 1},
                        {"text": "好的再见", "start": 1, "end": 2, "speaker": 0},
                    ],
                },
                config={},
                events=[],
                formats=["txt"],
                include_raw_candidates=False,
                include_config_snapshot=False,
            )

            output = (Path(tmpdir) / "transcript.txt").read_text(encoding="utf-8-sig")

        # 必须有说话人标签（说明走了 render_txt(segments)）
        self.assertIn("[spk=1]", output)
        self.assertIn("[spk=0]", output)
        self.assertIn("你好世界", output)
        self.assertIn("好的再见", output)
        # 段落之间应该有空行
        self.assertIn("\r\n\r\n", output.replace("\n\n", "\r\n\r\n"))

    def test_refined_text_as_separate_transcript_refined(self):
        """refined_text 应作为 transcript_refined.txt 单独导出（非主 transcript.txt）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_service.write_workflow_artifacts(
                output_dir=tmpdir,
                result={
                    "text": "原始全文",
                    "refined_text": "校对润色后的全文",
                    "segments": [{"text": "原始段", "start": 0, "end": 1}],
                },
                config={},
                events=[],
                formats=["txt"],
                include_raw_candidates=False,
                include_config_snapshot=False,
            )

            names = {p.name for p in Path(tmpdir).glob("*.txt")}
            self.assertIn("transcript.txt", names)
            self.assertIn("transcript_refined.txt", names)
            refined_content = (Path(tmpdir) / "transcript_refined.txt").read_text(encoding="utf-8-sig")
            self.assertEqual(refined_content.strip(), "校对润色后的全文")


if __name__ == "__main__":
    unittest.main()
