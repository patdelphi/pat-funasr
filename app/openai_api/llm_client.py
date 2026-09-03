# -*- coding: utf-8 -*-
"""
程序说明：
LLM 客户端公共模块——OpenAI 兼容接口调用 + 熔断 + fallback 链。

所有需要调用外部 LLM 的路径统一使用本模块的 call_llm()，
保证熔断、超时拆分、enable_thinking=False、fallback 链全部生效。

可配置项（通过 .env 或直接传参）：
  - connect_timeout (default 10s)：建连超时，DNS 不通/防火墙拒绝时快速失败
  - read_timeout   (default 300s)：响应读取超时
  - fuse_after_fails (default 2) ：连续失败 N 次触发熔断
  - fuse_duration   (default 300s)：熔断持续时间
  - max_tokens      (default 8192)：输出 token 额度，避免长文本被截断
  - enable_thinking (default False)：关闭推理模式（qwen3 系列有效）
  - fallback_chain  (default [])  ：主模型失败时依次尝试的备用 (base_url, api_key, model) 列表

所有配置从 .env 读取时，调用方只需传 base_url/api_key/model；
fallback 链自动从 .env 中已启用的 LLM Provider 构建。
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# ---- 默认值 ----
_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
_DEFAULT_MODEL = "qwen2.5:7b"
_DEFAULT_API_KEY = "no-key"
_DEFAULT_TIMEOUT_CONNECT = 10
_DEFAULT_TIMEOUT_READ = 300
_DEFAULT_MAX_TOKENS = 8192
_DEFAULT_ENABLE_THINKING = False
_DEFAULT_TEMPERATURE = 0.3

# ---- 熔断参数 ----
_FUSE_TRIP_AFTER_FAILS = 2
_FUSE_DURATION_SECONDS = 300
_FUSE_PASS_RESULT = ""

# ---- 熔断状态（进程内全局） ----
# key: (base_url.rstrip('/'), model)
# value: {'fail_streak': int, 'open_until': 0.0, 'last_reason': str}
_fuse_state: Dict[Tuple[str, str], Dict[str, Any]] = {}


def _get_fuse_state(key: Tuple[str, str]) -> Dict[str, Any]:
    return _fuse_state.setdefault(key, {"fail_streak": 0, "open_until": 0.0, "last_reason": ""})


def _is_fused(key: Tuple[str, str]) -> Optional[str]:
    """
    返回 None 表示未熔断，否则返回熔断原因字符串。

    熔断过期（open_until > 0 且 <= now）时重置熔断状态，
    但保留 fail_streak（不要重置）——只有真正触发过熔断后才重置，
    未触发过熔断的累积失败不应被意外清零。
    """
    state = _fuse_state.get(key)
    if state is None:
        return None
    now = time.time()
    open_until = state.get("open_until", 0)
    if open_until > 0 and open_until > now:
        # 熔断激活中
        remain = int(open_until - now)
        reason = state.get("last_reason") or "连续失败"
        return f"熔断激活 剩余{remain}s 原因: {reason}"
    if open_until > 0 and open_until <= now:
        # 熔断已过期，只重置 open_until，保留 fail_streak 让后续连续失败能正常触发熔断
        state["open_until"] = 0.0
    return None


def _mark_fail(key: Tuple[str, str], reason: str) -> None:
    s = _get_fuse_state(key)
    s["fail_streak"] = int(s.get("fail_streak") or 0) + 1
    s["last_reason"] = reason
    failure_time = time.time()
    if s["fail_streak"] >= _FUSE_TRIP_AFTER_FAILS and s["open_until"] <= failure_time:
        s["open_until"] = failure_time + _FUSE_DURATION_SECONDS
        logger.warning(
            "LLM 熔断触发[%s/%s] 连续失败 %d 次 5min 内跳过 最近原因: %s",
            key[0], key[1], s["fail_streak"], reason,
        )


def _mark_ok(key: Tuple[str, str]) -> None:
    s = _fuse_state.get(key)
    if s is not None:
        s["fail_streak"] = 0
        s["open_until"] = 0.0


def _call_one(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    system_prompt: str,
    temperature: float,
    connect_timeout: int,
    read_timeout: int,
    max_tokens: int,
    enable_thinking: bool,
) -> str:
    """
    单次 LLM 调用（内部方法，不处理熔断/fallback）。
    返回 content 字符串；异常时返回空串。
    """
    key = (base_url.rstrip("/"), model)
    try:
        timeout_tuple = (connect_timeout, read_timeout)
        resp = requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt or "你是一个专业转写助手。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "enable_thinking": enable_thinking,
            },
            timeout=timeout_tuple,
        )
        if resp.status_code != 200:
            _mark_fail(key, f"HTTP {resp.status_code}: {resp.text[:200]}")
            logger.error("LLM 调用失败 [%s/%s] status=%s body=%s",
                         base_url, model, resp.status_code, resp.text[:300])
            return ""
        data = resp.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        if not content:
            _mark_fail(key, "空响应 content")
            logger.error("LLM 返回空内容 [%s/%s]", base_url, model)
            return ""
        _mark_ok(key)
        return content
    except requests.exceptions.ConnectTimeout:
        reason = f"连接超时({connect_timeout}s)"
        _mark_fail(key, reason)
        logger.error("LLM %s [%s/%s]", reason, base_url, model)
        return ""
    except requests.exceptions.ReadTimeout:
        reason = f"响应超时({read_timeout}s)"
        _mark_fail(key, reason)
        logger.error("LLM %s [%s/%s]", reason, base_url, model)
        return ""
    except requests.exceptions.ConnectionError as exc:
        reason = f"连接错误: {str(exc)[:120]}"
        _mark_fail(key, reason)
        logger.error("LLM %s [%s/%s]", reason, base_url, model)
        return ""
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        reason = f"响应解析错误: {str(exc)[:120]}"
        _mark_fail(key, reason)
        logger.error("LLM %s [%s/%s]", reason, base_url, model)
        return ""
    except Exception as exc:
        reason = f"未知异常: {type(exc).__name__}: {str(exc)[:120]}"
        _mark_fail(key, reason)
        logger.error("LLM %s [%s/%s]", reason, base_url, model, exc_info=True)
        return ""


def build_fallback_chain(
    base_url: str,
    api_key: str,
    model: str,
    env_prefixes: Optional[List[str]] = None,
) -> List[Tuple[str, str, str]]:
    """
    从 .env 读取已启用的 LLM Provider，构建 fallback 链。

    主模型失败时依次尝试 fallback 链中的 (base_url, api_key, model)。
    默认读取 LLM_2_* / LLM_3_* / ... 配置。

    返回列表：[(base_url, api_key, model), ...]，不包含主模型自身。
    """
    if env_prefixes is None:
        # 自动扫描 LLM_2 ~ LLM_9
        env_prefixes = []
        for i in range(2, 10):
            enabled = os.environ.get(f"LLM_{i}_ENABLED", "").strip().lower()
            if enabled in ("true", "1", "yes"):
                env_prefixes.append(f"LLM_{i}")

    chain: List[Tuple[str, str, str]] = []
    for prefix in env_prefixes:
        fb_base_url = os.environ.get(f"{prefix}_BASE_URL", "").strip()
        fb_api_key = os.environ.get(f"{prefix}_API_KEY", "").strip()
        fb_models_str = os.environ.get(f"{prefix}_MODELS", "").strip()
        if not fb_base_url or not fb_api_key or not fb_models_str:
            continue
        for fb_model in [m.strip() for m in fb_models_str.split(",") if m.strip()]:
            # 跳过与主模型相同的条目
            if (fb_base_url.rstrip("/"), fb_model) == (base_url.rstrip("/"), model):
                continue
            chain.append((fb_base_url, fb_api_key, fb_model))
    return chain


def call_llm(
    prompt: str,
    system_prompt: str = "",
    base_url: str = _DEFAULT_BASE_URL,
    api_key: str = _DEFAULT_API_KEY,
    model: str = _DEFAULT_MODEL,
    temperature: float = _DEFAULT_TEMPERATURE,
    timeout: int = _DEFAULT_TIMEOUT_READ,
    connect_timeout: int = _DEFAULT_TIMEOUT_CONNECT,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    enable_thinking: bool = _DEFAULT_ENABLE_THINKING,
    fallback_chain: Optional[List[Tuple[str, str, str]]] = None,
    enable_fallback: bool = True,
) -> str:
    """
    调用 LLM（OpenAI 兼容接口），内置熔断 + fallback 链。

    参数说明：
      - timeout: 响应读取超时秒数（默认 300s）
      - connect_timeout: 建连超时秒数（默认 10s）
      - max_tokens: 输出 token 额度（默认 8192，防止长文本被截断）
      - enable_thinking: 关闭推理模式（qwen3 系列有效，避免 reasoning_tokens 耗尽 content）
      - fallback_chain: 备用 (base_url, api_key, model) 列表。None 时自动从 .env 构建
      - enable_fallback: 是否启用 fallback（默认 True；设 False 则只用主模型）

    返回：
      - 成功：模型输出文本
      - 熔断激活：空串（日志记录熔断信息）
      - 全部失败：空串（日志记录最后一次失败原因）
    """
    # 自动构建 fallback 链
    if fallback_chain is None and enable_fallback:
        fallback_chain = build_fallback_chain(base_url, api_key, model)

    # 依次尝试：主模型 → fallback 链
    candidates = [(base_url, api_key, model)]
    if enable_fallback and fallback_chain:
        candidates.extend(fallback_chain)

    last_reason = ""
    for idx, (fb_url, fb_key, fb_model) in enumerate(candidates):
        key = (fb_url.rstrip("/"), fb_model)
        fused_reason = _is_fused(key)
        if fused_reason:
            logger.warning("LLM[%d] %s/%s 跳过：%s", idx, fb_url, fb_model, fused_reason)
            last_reason = fused_reason
            continue
        logger.info("LLM[%d] 调用 %s/%s", idx, fb_url, fb_model)
        result = _call_one(
            base_url=fb_url,
            api_key=fb_key,
            model=fb_model,
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            connect_timeout=connect_timeout,
            read_timeout=timeout,
            max_tokens=max_tokens,
            enable_thinking=enable_thinking,
        )
        if result:
            if idx > 0:
                logger.warning("LLM fallback[%d] %s/%s 成功", idx, fb_url, fb_model)
            return result
        # 失败，记下原因（已在 _call_one 内写日志）
        last_reason = f"{fb_url}/{fb_model} 返回空"

    # 全部候选都失败
    logger.error("LLM 全部候选失败：%s", last_reason or "未知原因")
    return _FUSE_PASS_RESULT
