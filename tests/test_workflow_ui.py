"""
程序说明：
验证精细转录前端将全部显式选项转换为后端 workflow schema，并稳定渲染事件日志。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_WEBUI_DIR = _ROOT / "app" / "pat_funasr_webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))

from workflow_ui import (  # noqa: E402
    build_workflow_config,
    render_workflow_event_panel,
    render_workflow_events,
)


class TestWorkflowUi(unittest.TestCase):
    def test_builder_preserves_user_model_and_stage_choices(self):
        config = build_workflow_config(
            {
                "primary_model": "qwen3-asr",
                "transcription_mode": "multi_model",
                "reviewer_models": ["sensevoice", "paraformer"],
                "execution": "parallel",
                "max_concurrency": 2,
                "diarization_enabled": True,
                "diarization_asr_model": "paraformer",
                "speaker_model": "cam++",
                "llm_proofread_enabled": True,
                "llm_proofread_selection": "2|proof-model",
                "llm_proofread_scope": "segments",
                "llm_proofread_template_id": "strict",
                "translation_enabled": True,
                "translation_model": "nllb-200-distilled-600m",
                "source_lang": "zho_Hans",
                "target_lang": "eng_Latn",
                "export_formats": ["json", "srt"],
            }
        )
        self.assertEqual(config["transcription"]["primary"]["model"], "qwen3-asr")
        self.assertEqual(
            [item["model"] for item in config["transcription"]["reviewers"]],
            ["sensevoice", "paraformer"],
        )
        self.assertEqual(config["llm_proofread"]["provider_profile_id"], "2")
        self.assertEqual(config["llm_proofread"]["model"], "proof-model")
        self.assertEqual(config["llm_proofread"]["scope"], "segments")
        self.assertEqual(config["llm_proofread"]["template_id"], "strict")
        self.assertEqual(config["translation"]["target_lang"], "eng_Latn")

    def test_event_renderer_keeps_errors_and_stage_progress(self):
        text = render_workflow_events(
            [
                {"event_id": 1, "level": "progress", "stage": "transcription.primary", "progress": 0.5, "message": "处理中", "model": "qwen"},
                {"event_id": 2, "level": "error", "stage": "translation", "progress": 0.7, "message": "失败", "error_code": "MODEL_ERROR"},
            ]
        )
        self.assertIn("[50%]", text)
        self.assertIn("transcription.primary", text)
        self.assertIn("ERROR", text)
        self.assertIn("MODEL_ERROR", text)

    def test_event_panel_has_controls_and_escapes_messages(self):
        panel = render_workflow_event_panel(
            [{"event_id": 1, "level": "error", "message": "</script><img onerror=x>"}]
        )

        self.assertIn("暂停/继续滚动", panel)
        self.assertIn("仅警告/错误", panel)
        self.assertIn("下载日志", panel)
        self.assertNotIn("</script><img", panel)
        self.assertIn("&lt;/script&gt;", panel)


if __name__ == "__main__":
    unittest.main()
