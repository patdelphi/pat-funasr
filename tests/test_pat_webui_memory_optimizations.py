# -*- coding: utf-8 -*-
"""
程序说明：
Pat WebUI 的前端预览限长与 Streaming 预览收敛测试。
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_APP_PATH = _ROOT / "app" / "pat_funasr_webui" / "gradio_app.py"
_SPEC = importlib.util.spec_from_file_location("funasr_pat_webui_gradio_app", _APP_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"无法加载 Pat WebUI Gradio 入口：{_APP_PATH}")
gradio_app = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gradio_app)

PREVIEW_MAX_CHARS = gradio_app.PREVIEW_MAX_CHARS
RAW_JSON_PREVIEW_MAX_CHARS = gradio_app.RAW_JSON_PREVIEW_MAX_CHARS
STREAMING_PREVIEW_MAX_CHARS = gradio_app.STREAMING_PREVIEW_MAX_CHARS
format_streaming_preview_text = gradio_app.format_streaming_preview_text
limit_preview_text = gradio_app.limit_preview_text
limit_raw_json_preview = gradio_app.limit_raw_json_preview
truncate_tail_text = gradio_app.truncate_tail_text


class TestPatWebuiMemoryOptimizations(unittest.TestCase):
    def test_truncate_tail_text_keeps_short(self):
        text = "abc"
        self.assertEqual(truncate_tail_text(text, 10), text)

    def test_truncate_tail_text_truncates_tail(self):
        text = "x" * 50 + "TAIL"
        truncated = truncate_tail_text(text, 8)
        self.assertTrue(truncated.endswith("xTAIL"))
        self.assertIn("已截断", truncated)

    def test_limit_preview_text_uses_default(self):
        text = "x" * (PREVIEW_MAX_CHARS + 10) + "TAIL"
        truncated = limit_preview_text(text)
        self.assertIn("已截断", truncated)
        self.assertTrue(truncated.endswith(text[-PREVIEW_MAX_CHARS:]))

    def test_limit_raw_json_preview_uses_default(self):
        text = "x" * (RAW_JSON_PREVIEW_MAX_CHARS + 10) + "TAIL"
        truncated = limit_raw_json_preview(text)
        self.assertIn("已截断", truncated)
        self.assertTrue(truncated.endswith(text[-RAW_JSON_PREVIEW_MAX_CHARS:]))

    def test_format_streaming_preview_text_truncates(self):
        text = "x" * (STREAMING_PREVIEW_MAX_CHARS + 10) + "TAIL"
        preview = format_streaming_preview_text(text, final_flag=False)
        self.assertIn("已截断", preview)
        self.assertTrue(preview.endswith(text[-STREAMING_PREVIEW_MAX_CHARS:]))


if __name__ == "__main__":
    unittest.main()
