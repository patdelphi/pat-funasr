# -*- coding: utf-8 -*-
"""
纪要+思维导图 LLM 生成模块
使用 requests 调用 OpenAI 兼容接口(支持 Ollama/OpenAI/Claude)
优化点：
  - timeout 拆分为 connect / read 两段：连接失败 10s 内快速失败，避免长音频场景每块空等 5min
  - 连续失败熔断：同一 (base_url, model) 连续 2 次失败（超时/非200/空响应/网络异常）
    后直接短路返回空串，持续 5min 后自动重试，避免分块 LLM 场景下 N 块都等满超时
"""
import json
import logging
import time
from typing import Optional, Tuple, Dict, Any

import requests

logger = logging.getLogger(__name__)

# 默认 LLM 配置
_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"  # Ollama 默认地址
_DEFAULT_MODEL = "qwen2.5:7b"
_DEFAULT_TIMEOUT_CONNECT = 10   # TCP 连接阶段快速失败：10s（网络不通/ DNS 错 10s 就退出）
_DEFAULT_TIMEOUT_READ = 300     # 响应读取阶段 5min（大推理允许慢）

# ---- 熔断状态（进程内全局） ----
# key: (base_url.rstrip('/'), model)
# value: {'fail_streak': int,  'open_until': 0.0,  'last_reason': str}
_fuse_state: Dict[Tuple[str, str], Dict[str, Any]] = {}
_FUSE_TRIP_AFTER_FAILS = 2   # 连续失败 2 次 → 熔断
_FUSE_DURATION_SECONDS = 300  # 熔断持续 5 分钟
_FUSE_PASS_RESULT = ""        # 熔断期间返回值（空串，与异常分支保持一致）


def call_llm(
    prompt: str,
    system_prompt: str = "",
    base_url: str = _DEFAULT_BASE_URL,
    api_key: str = "no-key",
    model: str = _DEFAULT_MODEL,
    temperature: float = 0.3,
    timeout: int = _DEFAULT_TIMEOUT_READ,
    connect_timeout: int = _DEFAULT_TIMEOUT_CONNECT,
) -> str:
    """
    调用 LLM（OpenAI 兼容接口）
    - timeout: 响应读取超时秒数（默认 300s）
    - connect_timeout: 建连超时秒数（默认 10s），DNS 不通/防火墙拒绝时快速失败
    - 内置连续失败熔断：同 (base_url, model) 失败 2 次后 5min 内直接返回空串
    返回模型输出文本，异常时返回空字符串
    """
    # ---- 熔断判断 ----
    key = (base_url.rstrip("/"), model)
    state = _fuse_state.get(key)
    now = time.time()
    if state is not None and state["open_until"] > now:
        remain_sec = int(state["open_until"] - now)
        reason = state.get("last_reason") or "连续失败"
        logger.warning("LLM 熔断激活[%s/%s] 剩余%ds，跳过调用。原因: %s",
                       base_url, model, remain_sec, reason)
        return _FUSE_PASS_RESULT
    # 熔断过期，重置失败计数
    if state is not None and state["open_until"] and state["open_until"] <= now:
        state["open_until"] = 0.0
        state["fail_streak"] = 0

    def _mark_fail(reason: str):
        s = _fuse_state.setdefault(key, {"fail_streak": 0, "open_until": 0.0, "last_reason": ""})
        s["fail_streak"] = int(s.get("fail_streak") or 0) + 1
        s["last_reason"] = reason
        failure_time = time.time()
        if s["fail_streak"] >= _FUSE_TRIP_AFTER_FAILS and s["open_until"] <= failure_time:
            # 慢请求可能已运行数分钟，熔断窗口必须从实际失败时刻开始。
            s["open_until"] = failure_time + _FUSE_DURATION_SECONDS
            logger.warning("LLM 熔断触发[%s/%s]：连续失败 %d 次，5min 内跳过后续调用。最近原因: %s",
                           base_url, model, s["fail_streak"], reason)

    def _mark_ok():
        s = _fuse_state.get(key)
        if s is not None:
            s["fail_streak"] = 0
            s["open_until"] = 0.0

    try:
        timeout_tuple = (connect_timeout, timeout)
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
                # 增大输出 token 额度，避免长文本优化/纪要/思维导图生成时被截断
                "max_tokens": 8192,
                # 关闭推理模式（dashscope qwen3 系列有效，避免 reasoning_tokens
                # 耗尽 max_tokens 导致 content 为空；其他 OpenAI 兼容 API 忽略此字段）
                "enable_thinking": False,
            },
            timeout=timeout_tuple,
        )
        if resp.status_code != 200:
            _mark_fail(f"HTTP {resp.status_code}: {resp.text[:200]}")
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
            _mark_fail("空响应 content")
            logger.error("LLM 返回空内容 [%s/%s]", base_url, model)
            return ""
        _mark_ok()
        return content
    except requests.exceptions.ConnectTimeout:
        reason = f"连接超时({connect_timeout}s)"
        _mark_fail(reason)
        logger.error("LLM %s [%s/%s]", reason, base_url, model)
        return ""
    except requests.exceptions.ReadTimeout:
        reason = f"响应超时({timeout}s)"
        _mark_fail(reason)
        logger.error("LLM %s [%s/%s]", reason, base_url, model)
        return ""
    except requests.exceptions.Timeout:
        reason = f"调用超时({connect_timeout}s+{timeout}s)"
        _mark_fail(reason)
        logger.error("LLM %s [%s/%s]", reason, base_url, model)
        return ""
    except requests.exceptions.RequestException as e:
        reason = f"网络错误:{e.__class__.__name__}"
        _mark_fail(reason)
        logger.error("LLM %s [%s/%s] %s", reason, base_url, model, e)
        return ""
    except Exception as e:
        reason = f"异常:{e.__class__.__name__}:{e}"
        _mark_fail(reason)
        logger.exception("LLM 调用未知异常 [%s/%s]", base_url, model)
        return ""


def chunk_text(text: str, chunk_size: int = 5000, overlap: int = 1000) -> list[str]:
    """将长文本分段，带 overlap"""
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def generate_summary(
    transcript_text: str,
    summary_prompt: str,
    base_url: str = _DEFAULT_BASE_URL,
    api_key: str = "no-key",
    model: str = _DEFAULT_MODEL,
) -> dict:
    """
    生成结构化纪要（同步一次性，保留旧接口）
    返回 JSON dict，解析失败返回空 dict
    """
    collected = {}
    for evt, data in generate_summary_streaming(
        transcript_text, summary_prompt,
        base_url=base_url, api_key=api_key, model=model,
    ):
        if evt == "done":
            collected = data
    return collected


def generate_summary_streaming(
    transcript_text: str,
    summary_prompt: str,
    base_url: str = _DEFAULT_BASE_URL,
    api_key: str = "no-key",
    model: str = _DEFAULT_MODEL,
):
    """流式生成纪要，生成器 yield：
        ("chunk_start", {"idx":1-based, "total":N})
        ("chunk_done",  {"idx":1-based, "total":N, "success":bool, "summary_so_far":dict})
        ("agg_start",   {})
        ("done",        dict_final_summary)
    """
    if not summary_prompt:
        yield "done", {}
        return
    chunks = chunk_text(transcript_text)
    summaries = []
    total = len(chunks)
    for i, chunk in enumerate(chunks):
        yield "chunk_start", {"idx": i + 1, "total": total}
        prompt = f"{summary_prompt}\n\n--- 转写文本(第{i+1}/{total}段) ---\n{chunk}"
        result = call_llm(prompt, base_url=base_url, api_key=api_key, model=model)
        ok = bool(result)
        if ok:
            summaries.append(result)
        # 阶段性聚合（让用户即便中途也能看到东西）
        interim = {}
        if len(summaries) == 1:
            interim = _parse_json_response(summaries[0])
        elif summaries:
            interim = _aggregate_summaries(summaries)
        yield "chunk_done", {"idx": i + 1, "total": total, "success": ok,
                             "summary_so_far": interim}

    if len(summaries) == 1:
        final = _parse_json_response(summaries[0])
    else:
        yield "agg_start", {}
        final = _aggregate_summaries(summaries)
    yield "done", final


def generate_mindmap(
    transcript_text: str,
    mindmap_prompt: str,
    base_url: str = _DEFAULT_BASE_URL,
    api_key: str = "no-key",
    model: str = _DEFAULT_MODEL,
) -> dict:
    """生成思维导图 JSON 树（同步一次性，保留旧接口）
    返回 {title, children:[...]} 格式 dict
    """
    collected = {}
    for evt, data in generate_mindmap_streaming(
        transcript_text, mindmap_prompt,
        base_url=base_url, api_key=api_key, model=model,
    ):
        if evt == "done":
            collected = data
    return collected


def generate_mindmap_streaming(
    transcript_text: str,
    mindmap_prompt: str,
    base_url: str = _DEFAULT_BASE_URL,
    api_key: str = "no-key",
    model: str = _DEFAULT_MODEL,
):
    """流式生成思维导图（使用全量文本而非前2段），生成器 yield：
        ("chunk_start", {"idx":1-based, "total":N})
        ("chunk_done",  {"idx":1-based, "total":N, "success":bool, "mindmap_so_far":dict})
        ("agg_start",   {})
        ("done",        dict_final_mindmap)
    """
    if not mindmap_prompt:
        yield "done", {}
        return
    chunks = chunk_text(transcript_text, chunk_size=6000, overlap=500)
    total = len(chunks)
    if total <= 1:
        yield "chunk_start", {"idx": 1, "total": 1}
        prompt = f"{mindmap_prompt}\n\n--- 转写文本 ---\n{transcript_text}"
        result = call_llm(prompt, base_url=base_url, api_key=api_key, model=model)
        ok = bool(result)
        parsed = _parse_json_response(result) if ok else {}
        yield "chunk_done", {"idx": 1, "total": 1, "success": ok, "mindmap_so_far": parsed}
        yield "done", parsed
        return

    sub_roots: list = []
    for i, chunk in enumerate(chunks):
        yield "chunk_start", {"idx": i + 1, "total": total}
        prompt = f"{mindmap_prompt}\n\n--- 转写文本(第{i+1}/{total}段) ---\n{chunk}"
        result = call_llm(prompt, base_url=base_url, api_key=api_key, model=model)
        parsed = _parse_json_response(result) if result else {}
        ok = bool(parsed and (parsed.get("title") or parsed.get("children")))
        if ok:
            sub_roots.append(parsed)
        # 阶段展示：截至目前的合并
        interim = {}
        if len(sub_roots) == 1:
            interim = sub_roots[0]
        elif sub_roots:
            merged = []
            for r in sub_roots:
                if r.get("children"):
                    merged.extend(r["children"])
                elif r.get("title"):
                    merged.append({"title": r["title"],
                                   "children": r.get("children") or []})
            interim = {"title": sub_roots[0].get("title") or "思维导图",
                       "children": merged}
        yield "chunk_done", {"idx": i + 1, "total": total, "success": ok,
                             "mindmap_so_far": interim}

    yield "agg_start", {}
    if not sub_roots:
        yield "done", {}
    elif len(sub_roots) == 1:
        yield "done", sub_roots[0]
    else:
        merged_children = []
        for r in sub_roots:
            if r.get("children"):
                merged_children.extend(r["children"])
            elif r.get("title"):
                merged_children.append(
                    {"title": r["title"], "children": r.get("children") or []}
                )
        final = {"title": sub_roots[0].get("title") or "思维导图",
                 "children": merged_children}
        yield "done", final


def refine_transcript(
    asr_text: str,
    llm_prompt: str,
    hotwords: list = None,
    base_url: str = _DEFAULT_BASE_URL,
    api_key: str = "no-key",
    model: str = _DEFAULT_MODEL,
) -> str:
    """
    LLM 二次优化转写文本（同步一次性，保留旧接口）
    返回润色后的纯文本
    """
    pieces = []
    for evt, data in refine_transcript_streaming(
        asr_text, llm_prompt, hotwords=hotwords,
        base_url=base_url, api_key=api_key, model=model,
    ):
        if evt == "piece_done":
            piece = data.get("piece") or ""
            if piece:
                pieces.append(piece)
        elif evt == "done":
            return data or ""
    return "\n".join(pieces)


def refine_transcript_streaming(
    asr_text: str,
    llm_prompt: str,
    hotwords: list = None,
    base_url: str = _DEFAULT_BASE_URL,
    api_key: str = "no-key",
    model: str = _DEFAULT_MODEL,
    chunk_size: int = 5000,
    overlap: int = 500,
):
    """分块 LLM 润色，生成器 yield：
        ("chunk_start", {"idx":1-based, "total":N})
        ("piece_done",  {"idx":1-based, "total":N, "success":bool,
                         "piece":str, "text_so_far":str})
        ("done",        str_final)
    """
    chunks = chunk_text(asr_text, chunk_size=chunk_size, overlap=overlap)
    if not chunks:
        yield "done", ""
        return
    hotword_str = "、".join(hotwords) if hotwords else "无"
    results: list[str] = []
    total = len(chunks)
    for i, chunk in enumerate(chunks):
        yield "chunk_start", {"idx": i + 1, "total": total}
        prompt = f"""{llm_prompt}

--- 专业词表 ---
{hotword_str}

--- ASR 原始转写(第{i + 1}/{total}段) ---
{chunk}
"""
        piece = call_llm(prompt, base_url=base_url, api_key=api_key, model=model)
        ok = bool(piece)
        if ok:
            results.append(piece)
        yield "piece_done", {
            "idx": i + 1, "total": total, "success": ok,
            "piece": piece or "",
            "text_so_far": "\n".join(results),
        }
    yield "done", "\n".join(results)


def _parse_json_response(text: str) -> dict:
    """从 LLM 输出中提取 JSON，兼容 markdown 代码块"""
    text = text.strip()
    # 去除 markdown 代码块
    if text.startswith("```"):
        lines = text.split("\n")
        # 去掉首尾 ``` 行
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 尝试找第一个 { 到最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        return {}


def _aggregate_summaries(summaries: list[str]) -> dict:
    """聚合多段摘要为统一 JSON"""
    result = {"aggregated": True, "parts": []}
    for i, s in enumerate(summaries):
        parsed = _parse_json_response(s)
        if parsed:
            result["parts"].append(parsed)
    return result
