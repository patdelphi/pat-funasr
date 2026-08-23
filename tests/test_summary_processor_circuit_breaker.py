"""
程序说明：
验证 LLM 熔断窗口从实际失败时刻开始计算，避免慢请求消耗熔断有效期。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


_ROOT = Path(__file__).resolve().parents[1]
_WEBUI_DIR = _ROOT / "app" / "pat_funasr_webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))

from fine_transcription import summary_processor  # noqa: E402


class _FailedResponse:
    status_code = 500
    text = "failed"


class TestSummaryProcessorCircuitBreaker(unittest.TestCase):
    def test_open_until_uses_failure_time(self):
        summary_processor._fuse_state.clear()
        with patch.object(summary_processor.requests, "post", return_value=_FailedResponse()), patch.object(
            summary_processor.logger, "error"
        ), patch.object(summary_processor.logger, "warning"), patch.object(
            summary_processor.time, "time", side_effect=[100.0, 110.0, 200.0, 220.0]
        ):
            summary_processor.call_llm("a", base_url="http://local", model="m")
            summary_processor.call_llm("b", base_url="http://local", model="m")
        state = summary_processor._fuse_state[("http://local", "m")]
        self.assertEqual(state["open_until"], 220.0 + summary_processor._FUSE_DURATION_SECONDS)


if __name__ == "__main__":
    unittest.main()
