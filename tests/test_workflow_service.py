"""
程序说明：
工作流配置、任务状态和事件协议测试。

目标：
- 验证多模型、时间戳和说话人配置依赖。
- 验证任务进度单调、warning/error 事件不会被覆盖。
- 验证任务取消、完成和事件日志快照。
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_OPENAI_API_DIR = _ROOT / "app" / "openai_api"
if str(_OPENAI_API_DIR) not in sys.path:
    sys.path.insert(0, str(_OPENAI_API_DIR))

from workflow_service import (  # noqa: E402
    WorkflowConfigError,
    WorkflowJobManager,
    parse_workflow_config,
    validate_workflow_config,
)


MODEL_CAPABILITIES = {
    "primary": {"offline_asr": True, "diarization": False, "forced_alignment": False},
    "reviewer": {"offline_asr": True, "diarization": True},
    "aligning": {"offline_asr": True, "diarization": False, "forced_alignment": True},
    "streaming": {"offline_asr": False, "streaming_asr": True},
}


class TestWorkflowConfig(unittest.TestCase):
    def test_multi_model_requires_reviewer_and_unique_models(self):
        config = parse_workflow_config(
            {
                "transcription": {
                    "mode": "multi_model",
                    "primary": {"model": "primary"},
                    "reviewers": [],
                }
            }
        )
        errors, _warnings = validate_workflow_config(config, MODEL_CAPABILITIES)
        self.assertIn("MULTI_MODEL_REVIEWER_REQUIRED", {item["code"] for item in errors})

        duplicate = parse_workflow_config(
            {
                "transcription": {
                    "mode": "multi_model",
                    "primary": {"model": "primary"},
                    "reviewers": [{"model": "primary"}],
                }
            }
        )
        errors, _warnings = validate_workflow_config(duplicate, MODEL_CAPABILITIES)
        self.assertIn("DUPLICATE_TRANSCRIPTION_MODEL", {item["code"] for item in errors})

    def test_rejects_model_without_offline_asr(self):
        config = parse_workflow_config(
            {"transcription": {"primary": {"model": "streaming"}}}
        )
        errors, _warnings = validate_workflow_config(config, MODEL_CAPABILITIES)
        self.assertIn("MODEL_CAPABILITY_MISMATCH", {item["code"] for item in errors})

    def test_diarization_and_subtitles_require_timestamps(self):
        config = parse_workflow_config(
            {
                "transcription": {"primary": {"model": "primary"}},
                "timestamps": {"level": "off"},
                "diarization": {
                    "enabled": True,
                    "asr_model": "reviewer",
                },
                "export": {"formats": ["srt"]},
            }
        )
        errors, _warnings = validate_workflow_config(config, MODEL_CAPABILITIES)
        codes = {item["code"] for item in errors}
        self.assertIn("DIARIZATION_REQUIRES_TIMESTAMPS", codes)
        self.assertIn("SUBTITLE_REQUIRES_TIMESTAMPS", codes)

    def test_parse_rejects_unknown_fields(self):
        with self.assertRaises(WorkflowConfigError):
            parse_workflow_config({"unknown_stage": {"enabled": True}})

    def test_forced_alignment_requires_capable_primary_model(self):
        config = parse_workflow_config(
            {
                "transcription": {"primary": {"model": "primary"}},
                "timestamps": {
                    "level": "word",
                    "forced_alignment": True,
                    "aligner_model": "Qwen/Qwen3-ForcedAligner-0.6B",
                },
            }
        )
        errors, _warnings = validate_workflow_config(config, MODEL_CAPABILITIES)
        self.assertIn("FORCED_ALIGNMENT_UNSUPPORTED_PRIMARY", {item["code"] for item in errors})

    def test_chunk_translation_and_emotion_dependencies_are_validated(self):
        capabilities = {
            **MODEL_CAPABILITIES,
            "translator": {"translation": True},
            "emotion": {"emotion": True},
        }
        config = parse_workflow_config(
            {
                "transcription": {"primary": {"model": "primary"}},
                "segmentation": {
                    "chunk_enabled": True,
                    "chunk_seconds": 30,
                    "overlap_seconds": 30,
                },
                "translation": {
                    "enabled": True,
                    "model": "translator",
                    "source_lang": "zho_Hans",
                    "target_lang": "zho_Hans",
                },
                "emotion": {"enabled": True, "model": "missing"},
            }
        )
        errors, _warnings = validate_workflow_config(config, capabilities)
        codes = {item["code"] for item in errors}
        self.assertIn("CHUNK_OVERLAP_INVALID", codes)
        self.assertIn("TRANSLATION_LANGUAGES_IDENTICAL", codes)
        self.assertIn("MODEL_NOT_FOUND", codes)


class TestWorkflowJobManager(unittest.TestCase):
    def test_events_are_append_only_and_progress_is_monotonic(self):
        manager = WorkflowJobManager(max_workers=1)

        def runner(context):
            context.emit(
                level="progress",
                stage="transcription.primary",
                progress=0.4,
                message="主模型处理中",
                model="primary",
            )
            context.emit(
                level="warning",
                stage="transcription.primary",
                progress=0.2,
                message="进度回退输入应被钳制",
                error_code="ASR_RETRY",
                retryable=True,
            )
            context.emit(
                level="success",
                stage="export",
                progress=1.0,
                message="完成",
            )
            return {"text": "ok"}

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                source = Path(tmpdir) / "audio.wav"
                source.write_bytes(b"audio")
                job_id = manager.submit(
                    config={"workflow_version": "1.0"},
                    source_path=str(source),
                    runner=runner,
                )
                snapshot = manager.wait_for_terminal(job_id, timeout=3)

            self.assertEqual(snapshot["status"], "completed")
            self.assertEqual(snapshot["result"], {"text": "ok"})
            progresses = [
                event["progress"]
                for event in snapshot["events"]
                if event["progress"] is not None
            ]
            self.assertEqual(progresses, sorted(progresses))
            self.assertTrue(any(event["level"] == "warning" for event in snapshot["events"]))
            self.assertTrue(any(event["level"] == "success" for event in snapshot["events"]))
        finally:
            manager.shutdown(wait=True)

    def test_cancelled_job_keeps_event_history(self):
        manager = WorkflowJobManager(max_workers=1)

        def runner(context):
            context.emit(level="info", stage="prepare", progress=0.1, message="准备")
            while not context.cancelled:
                time.sleep(0.01)
            context.raise_if_cancelled()

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                source = Path(tmpdir) / "audio.wav"
                source.write_bytes(b"audio")
                job_id = manager.submit(config={}, source_path=str(source), runner=runner)
                time.sleep(0.05)
                manager.cancel(job_id)
                snapshot = manager.wait_for_terminal(job_id, timeout=3)

            self.assertEqual(snapshot["status"], "cancelled")
            self.assertTrue(any(event["stage"] == "prepare" for event in snapshot["events"]))
            self.assertTrue(any(event["stage_status"] == "cancelled" for event in snapshot["events"]))
        finally:
            manager.shutdown(wait=True)

    def test_queue_summary_counts_statuses(self):
        manager = WorkflowJobManager(max_workers=1)
        release = __import__("threading").Event()

        def runner(context):
            release.wait(timeout=1)
            return {}

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                source = Path(tmpdir) / "audio.wav"
                source.write_bytes(b"audio")
                job_id = manager.submit(config={}, source_path=str(source), runner=runner)
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    if manager.get_snapshot(job_id)["status"] == "running":
                        break
                    time.sleep(0.01)
                summary = manager.queue_summary()
                self.assertEqual(summary["total"], 1)
                self.assertEqual(summary["status_counts"]["running"], 1)
                release.set()
                manager.wait_for_terminal(job_id, timeout=2)
        finally:
            release.set()
            manager.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
