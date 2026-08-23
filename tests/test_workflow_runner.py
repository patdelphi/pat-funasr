"""
程序说明：
验证真实精细转录工作流的阶段编排、多模型校对、说话人对齐和错误策略。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_OPENAI_API_DIR = _ROOT / "app" / "openai_api"
if str(_OPENAI_API_DIR) not in sys.path:
    sys.path.insert(0, str(_OPENAI_API_DIR))

from workflow_runner import WorkflowRuntime, run_workflow  # noqa: E402
from workflow_service import WorkflowJobManager  # noqa: E402


class TestWorkflowRunner(unittest.TestCase):
    def _run(self, config, runtime):
        manager = WorkflowJobManager(max_workers=1)
        self.addCleanup(lambda: manager.shutdown(wait=True))
        source = Path(self.tempdir.name) / "source.wav"
        source.write_bytes(b"audio")
        job_id = manager.submit(
            config=config,
            source_path=str(source),
            runner=lambda context: run_workflow(context, runtime),
        )
        return manager.wait_for_terminal(job_id, timeout=3)

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

    def test_multi_model_diarization_and_artifacts(self):
        calls = []

        def transcribe(path, model_config, workflow_config, progress_callback):
            calls.append(("transcribe", model_config.model))
            progress_callback(1, 1, "分块 1/1")
            text = "你好世界" if model_config.model == "primary" else "你好，世界"
            return {
                "model": model_config.model,
                "weight": model_config.weight,
                "text": text,
                "segments": [{"start": 0.0, "end": 2.0, "text": text}],
            }

        def diarize(path, config):
            calls.append(("diarize", config.speaker_model))
            return {"segments": [{"start": 0.0, "end": 2.0, "speaker": "S1"}]}

        def write_artifacts(**kwargs):
            calls.append(("artifacts", tuple(kwargs["formats"])))
            return [{"name": "transcript.json", "path": "X:/transcript.json", "format": "json"}]

        runtime = WorkflowRuntime(
            transcribe=transcribe,
            diarize=diarize,
            write_artifacts=write_artifacts,
        )
        snapshot = self._run(
            {
                "transcription": {
                    "mode": "multi_model",
                    "primary": {"model": "primary", "weight": 2},
                    "reviewers": [{"model": "reviewer", "weight": 1}],
                    "execution": "serial",
                },
                "diarization": {"enabled": True, "asr_model": "primary"},
                "export": {"formats": ["json"]},
            },
            runtime,
        )

        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(snapshot["result"]["segments"][0]["speaker"], "S1")
        self.assertEqual(snapshot["result"]["primary_model"], "primary")
        self.assertEqual(snapshot["result"]["artifacts"][0]["format"], "json")
        self.assertIn(("transcribe", "reviewer"), calls)
        stages = {event["stage"] for event in snapshot["events"]}
        self.assertTrue({"transcription.primary", "transcription.reviewers", "diarization", "reconciliation", "export"} <= stages)

    def test_disabled_optional_stages_are_not_called(self):
        called = []

        def transcribe(path, model_config, workflow_config, progress_callback):
            return {
                "model": model_config.model,
                "weight": 1,
                "text": "ok",
                "segments": [{"start": 0, "end": 1, "text": "ok"}],
            }

        runtime = WorkflowRuntime(
            transcribe=transcribe,
            llm_stage=lambda *args: called.append("llm"),
            translate=lambda *args: called.append("translation"),
            emotion=lambda *args: called.append("emotion"),
            write_artifacts=lambda **kwargs: [],
        )
        snapshot = self._run({"transcription": {"primary": {"model": "primary"}}}, runtime)
        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(called, [])

    def test_skip_failed_reviewer_keeps_primary_and_warning(self):
        def transcribe(path, model_config, workflow_config, progress_callback):
            if model_config.model == "reviewer":
                raise RuntimeError("reviewer unavailable")
            return {
                "model": "primary",
                "weight": 1,
                "text": "primary text",
                "segments": [{"start": 0, "end": 1, "text": "primary text"}],
            }

        runtime = WorkflowRuntime(transcribe=transcribe, write_artifacts=lambda **kwargs: [])
        snapshot = self._run(
            {
                "transcription": {
                    "mode": "multi_model",
                    "primary": {"model": "primary"},
                    "reviewers": [{"model": "reviewer"}],
                    "resource_failure_policy": "skip_failed_reviewer",
                }
            },
            runtime,
        )
        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(snapshot["result"]["text"], "primary text")
        self.assertTrue(
            any(event.get("error_code") == "REVIEWER_SKIPPED" for event in snapshot["events"])
        )

    def test_parallel_failure_policy_retries_failed_reviewer_serially(self):
        attempts = {"reviewer-a": 0, "reviewer-b": 0}

        def transcribe(path, model_config, workflow_config, progress_callback):
            model = model_config.model
            if model in attempts:
                attempts[model] += 1
                if attempts[model] == 1:
                    raise RuntimeError("simulated parallel resource failure")
            return {
                "model": model,
                "weight": 1,
                "text": model,
                "segments": [{"start": 0, "end": 1, "text": model}],
            }

        runtime = WorkflowRuntime(transcribe=transcribe, write_artifacts=lambda **kwargs: [])
        snapshot = self._run(
            {
                "transcription": {
                    "mode": "multi_model",
                    "primary": {"model": "primary"},
                    "reviewers": [{"model": "reviewer-a"}, {"model": "reviewer-b"}],
                    "execution": "parallel",
                    "max_concurrency": 2,
                    "resource_failure_policy": "fallback_to_serial",
                }
            },
            runtime,
        )
        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(attempts, {"reviewer-a": 2, "reviewer-b": 2})
        self.assertTrue(
            any(event.get("error_code") == "REVIEWER_SERIAL_RETRY" for event in snapshot["events"])
        )


if __name__ == "__main__":
    unittest.main()
