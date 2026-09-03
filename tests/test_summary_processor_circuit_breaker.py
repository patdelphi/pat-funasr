# -*- coding: utf-8 -*-
"""
程序说明：
验证 LLM 熔断窗口从实际失败时刻开始计算，避免慢请求消耗熔断有效期。
"""

from __future__ import annotations

import sys
import time as _real_time
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
        """
        验证熔断窗口从实际失败时刻计算，而非第一次调用时刻。

        关键：两次 call_llm 失败后，open_until 应该等于第二次失败时刻
        加上 _FUSE_DURATION_SECONDS。这防止了慢请求在执行期间"消耗"
        熔断窗口有效期。
        """
        from openai_api import llm_client

        summary_processor._fuse_state.clear()

        # 精确控制 _mark_fail 里的 time.time() 返回值
        _mark_fail_index = {"n": 0}
        _orig_mark_fail = llm_client._mark_fail

        def _controlled_mark_fail(key, reason):
            _mark_fail_index["n"] += 1
            # 临时替换 time.time() 让 _mark_fail 内部拿到正确的 failure_time
            _orig_time = llm_client.time.time
            def _fake_time():
                if _mark_fail_index["n"] == 1:
                    return 110.0  # 第一次失败时刻
                if _mark_fail_index["n"] == 2:
                    return 220.0  # 第二次失败时刻
                return _orig_time()
            llm_client.time.time = _fake_time
            try:
                _orig_mark_fail(key, reason)
            finally:
                llm_client.time.time = _orig_time

        with patch.object(summary_processor.requests, "post", return_value=_FailedResponse()), patch.object(
            summary_processor.logger, "error"
        ), patch.object(summary_processor.logger, "warning"):
            llm_client._mark_fail = _controlled_mark_fail
            try:
                summary_processor.call_llm("a", base_url="http://local", model="m")
                summary_processor.call_llm("b", base_url="http://local", model="m")
            finally:
                llm_client._mark_fail = _orig_mark_fail

        state = summary_processor._fuse_state[("http://local", "m")]
        # 熔断窗口从第二次失败时刻(220)开始算
        self.assertEqual(state["open_until"], 220.0 + summary_processor._FUSE_DURATION_SECONDS)

    def test_fallback_chain_enabled(self):
        """验证 fallback 链构建：只有启用的 LLM Provider 才进入链"""
        from openai_api.llm_client import build_fallback_chain

        # 清空环境变量中可能存在的 LLM_2/LLM_3 配置
        import os
        saved = {}
        for key in list(os.environ.keys()):
            if key.startswith("LLM_"):
                saved[key] = os.environ.pop(key)
        try:
            chain = build_fallback_chain("http://primary/v1", "key", "primary-model")
            self.assertEqual(len(chain), 0, "无启用 Provider 时 fallback 链应为空")
        finally:
            os.environ.update(saved)


if __name__ == "__main__":
    unittest.main()
