# -*- coding: utf-8 -*-
"""
纪要+思维导图 LLM 生成模块
使用 requests 调用 OpenAI 兼容接口(支持 Ollama/OpenAI/Claude)

注：LLM 客户端已下沉到 app/openai_api/llm_client.py，
本模块从公共模块导入 call_llm，保持向后兼容。
新代码直接 import app.openai_api.llm_client.call_llm 即可。
"""
import json
import logging
import time
from typing import Optional, List, Tuple, Any, Dict

# 从公共 LLM 客户端模块导入（熔断 + fallback 链在此模块实现）
from openai_api.llm_client import (  # noqa: E402
    call_llm,
    build_fallback_chain,
    _fuse_state,  # 暴露给旧测试和外部代码访问熔断状态
    _FUSE_TRIP_AFTER_FAILS,  # 暴露给旧代码引用
    _FUSE_DURATION_SECONDS,  # 暴露给旧代码引用
    _FUSE_PASS_RESULT,  # 暴露给旧代码引用
)
# 向后兼容：旧测试 mock summary_processor.requests.post
from openai_api import llm_client as _llm_client_mod  # noqa: E402
requests = _llm_client_mod.requests  # noqa: E402

logger = logging.getLogger(__name__)

# 保留这些常量供旧代码引用（已下沉到 llm_client，这里只是别名）
_DEFAULT_BASE_URL = 'http://127.0.0.1:11434/v1'
_DEFAULT_MODEL = 'qwen2.5:7b'



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
