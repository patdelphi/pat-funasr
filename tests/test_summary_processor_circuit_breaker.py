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
_APP_DIR = _ROOT / "app"
_WEBUI_DIR = _ROOT / "app" / "pat_funasr_webui"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))

from fine_transcription import summary_processor  # noqa: E402


class _FailedResponse:
    status_code = 500
    text = "failed"


class _OkResponse:
    status_code = 200
    text = "ok"

    def __init__(self, content: str):
        self.content = content

    def json(self):
        return {"choices": [{"message": {"content": self.content}}]}


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

    def test_circuit_open_short_circuits_requests(self):
        """熔断激活期：call_llm 直接短路返回空串，不发起任何网络请求。"""
        summary_processor._fuse_state.clear()
        calls = {"n": 0}

        def _fail_post(url, **kwargs):
            calls["n"] += 1
            return _FailedResponse()

        with patch.object(summary_processor.requests, "post", side_effect=_fail_post), patch.object(
            summary_processor.logger, "error"
        ), patch.object(summary_processor.logger, "warning"):
            # 连续两次失败触发熔断
            summary_processor.call_llm("a", base_url="http://local", model="m")
            summary_processor.call_llm("b", base_url="http://local", model="m")
            before = calls["n"]
            # 熔断激活中：第三次调用被短路，不发请求
            result = summary_processor.call_llm("c", base_url="http://local", model="m")
            self.assertEqual(calls["n"], before, "熔断激活期不应发起新的请求")
            self.assertEqual(result, "")

    def test_circuit_expired_recovers(self):
        """熔断过期后自动恢复：再次调用正常发起请求，成功路径清空熔断状态。"""
        summary_processor._fuse_state.clear()
        key = ("http://local", "m")
        # 直接放入一个已过期的熔断状态（open_until 远早于 now）
        summary_processor._fuse_state[key] = {
            "fail_streak": 2,
            "open_until": 0.001,
            "last_reason": "HTTP 500",
        }
        calls = {"n": 0}

        def _ok_post(url, **kwargs):
            calls["n"] += 1
            return _OkResponse("你好")

        with patch.object(summary_processor.requests, "post", side_effect=_ok_post), patch.object(
            summary_processor.logger, "warning"
        ):
            result = summary_processor.call_llm("c", base_url="http://local", model="m")
        self.assertEqual(calls["n"], 1, "熔断过期后应恢复正常调用")
        self.assertEqual(result, "你好")
        # 成功后会走 _mark_ok：熔断窗口清零、连续失败计数清零
        self.assertEqual(summary_processor._fuse_state[key]["open_until"], 0.0)
        self.assertEqual(summary_processor._fuse_state[key]["fail_streak"], 0)

    def test_mark_ok_resets_streak_and_window(self):
        """单次失败后成功：_mark_ok 重置 fail_streak 与熔断窗口。"""
        summary_processor._fuse_state.clear()
        key = ("http://local", "m")

        # 先一次失败（未触发熔断，fail_streak=1）
        with patch.object(summary_processor.requests, "post", return_value=_FailedResponse()), patch.object(
            summary_processor.logger, "error"
        ):
            summary_processor.call_llm("a", base_url="http://local", model="m")
        self.assertEqual(summary_processor._fuse_state[key]["fail_streak"], 1)

        # 再成功：计数与窗口都应重置
        with patch.object(summary_processor.requests, "post", return_value=_OkResponse("好")), patch.object(
            summary_processor.logger, "warning"
        ):
            result = summary_processor.call_llm("b", base_url="http://local", model="m")
        self.assertEqual(result, "好")
        self.assertEqual(summary_processor._fuse_state[key]["fail_streak"], 0, "成功后应重置连续失败计数")
        self.assertEqual(summary_processor._fuse_state[key]["open_until"], 0.0, "成功后应清除熔断窗口")

    def test_fallback_chain_switches_on_primary_failure(self):
        """主模型失败时真实切换 fallback 链，返回备用模型结果。"""
        summary_processor._fuse_state.clear()

        def _switch_post(url, **kwargs):
            body = kwargs.get("json", {})
            if body.get("model") == "primary-model":
                return _FailedResponse()
            return _OkResponse("备用模型结果")

        with patch.object(summary_processor.requests, "post", side_effect=_switch_post), patch.object(
            summary_processor.logger, "error"
        ), patch.object(summary_processor.logger, "warning"):
            result = summary_processor.call_llm(
                "prompt",
                base_url="http://primary/v1",
                model="primary-model",
                fallback_chain=[("http://fallback/v1", "key", "fb-model")],
            )
        self.assertEqual(result, "备用模型结果")
        # fallback 模型成功调用，不应累积失败（无失败记录时不创建熔断状态）
        self.assertNotIn(
            ("http://fallback/v1", "fb-model"), summary_processor._fuse_state
        )

    def test_fallback_all_fail_returns_empty(self):
        """主模型与 fallback 全部失败时返回空串，且各自累计失败计数。"""
        summary_processor._fuse_state.clear()
        with patch.object(summary_processor.requests, "post", return_value=_FailedResponse()), patch.object(
            summary_processor.logger, "error"
        ), patch.object(summary_processor.logger, "warning"):
            result = summary_processor.call_llm(
                "prompt",
                base_url="http://primary/v1",
                model="primary-model",
                fallback_chain=[("http://fallback/v1", "key", "fb-model")],
            )
        self.assertEqual(result, "")
        self.assertEqual(summary_processor._fuse_state[("http://primary/v1", "primary-model")]["fail_streak"], 1)
        self.assertEqual(summary_processor._fuse_state[("http://fallback/v1", "fb-model")]["fail_streak"], 1)


if __name__ == "__main__":
    unittest.main()
