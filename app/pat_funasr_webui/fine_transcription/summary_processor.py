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
    # 参数防护：chunk_size<=0 或 overlap>=chunk_size 会使切片步进不前进，导致死循环
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须 >= 0 且小于 chunk_size")
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
            interim = _parse_json_response(summaries[0]) or {"_raw": (summaries[0] or "")[:500], "_parse_error": True}
        elif summaries:
            interim = _aggregate_summaries(summaries)
        yield "chunk_done", {"idx": i + 1, "total": total, "success": ok,
                             "summary_so_far": interim}

    if len(summaries) == 1:
        final = _parse_json_response(summaries[0])
        if final is None:
            # 单 chunk 也解析失败：保留原始文本
            final = {"_raw": (summaries[0] or "")[:2000], "_parse_error": True}
            logger.warning("generate_summary_streaming: 单 chunk 摘要 JSON 解析失败")
    else:
        yield "agg_start", {}
        # 二次 LLM 聚合：让 LLM 把各 chunk 的摘要综合成一份完整纪要
        final = _llm_synthesize_summaries(
            summaries, summary_prompt,
            base_url=base_url, api_key=api_key, model=model,
        )
        if not final:
            # 兜底：LLM 聚合失败时退化为直接拼接
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
        # 二次 LLM 聚合：让 LLM 把各 chunk 的导图合并成一个
        final = _llm_synthesize_mindmap(
            sub_roots,
            base_url=base_url, api_key=api_key, model=model,
        )
        if not final:
            # 兜底：直拼所有 children
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


def _parse_json_response(text: str) -> dict | None:
    """从 LLM 输出中提取 JSON，兼容 markdown 代码块。

    Returns:
        dict: 解析成功的 JSON 对象
        None: 解析失败（明确区分"失败"和"成功但为空"）
    """
    text = (text or "").strip()
    if not text:
        return None
    # 去除 markdown 代码块
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
        # 如果是 list（LLM 有时直接输出数组），包一层
        if isinstance(obj, list):
            return {"items": obj}
        return None
    except json.JSONDecodeError:
        # 尝试找第一个 { 到最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                obj = json.loads(text[start:end + 1])
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass
        logger.warning("_parse_json_response 解析失败，原文前 200 字符: %s", text[:200])
        return None


def _aggregate_summaries(summaries: list[str]) -> dict:
    """聚合多段摘要为统一 JSON（兜底：直接拼接 parts）。

    解析失败的摘要不会被丢弃，而是以 {"_raw": "...原始文本...", "_parse_error": true} 形式保留。
    这样用户至少能看到 LLM 实际输出了什么，而不是一个空壳 JSON。
    """
    result = {"aggregated": True, "parts": []}
    for i, s in enumerate(summaries):
        parsed = _parse_json_response(s)
        if parsed is not None:
            result["parts"].append(parsed)
        else:
            # 解析失败也保留原始文本，带诊断标记
            result["parts"].append({
                "_raw": (s or "").strip()[:500],
                "_parse_error": True,
            })
            logger.warning("_aggregate_summaries: 第 %d 段摘要 JSON 解析失败，已保留原始文本", i + 1)
    return result


# 二次聚合 prompt：把多份局部摘要综合成一份完整纪要
_SYNTHESIZE_PROMPT = """综合以下多份局部会议摘要 JSON 数组，合并成一份完整的会议纪要。

要求：
1. 相同议题合并为一条 sections，相同行动项/决定去重
2. sections 按讨论逻辑或重要性排序
3. overall_summary 覆盖完整会议
4. action_items / decisions / open_questions 从所有段落抽取合并
5. participants 格式为 [{{"spk": "spk=N", "role": "角色"}}]，综合各 chunk 推断

严格沿用下方 JSON 结构，仅输出 JSON。

--- 原始摘要 ---
{summaries_json}

--- 输出 JSON 结构 ---
{{
  "meeting_title": "一句话会议主题",
  "participants": [{{"spk": "spk=N", "role": "角色推断"}}],
  "overall_summary": "3-5 句总览",
  "sections": [
    {{
      "topic": "议题名称",
      "summary": "2-5 句提炼后的结论",
      "key_points": ["要点"],
      "quotes": [{{"speaker": "spk=N", "timestamp": "MM:SS", "text": "原话"}}],
      "decisions": ["决定"],
      "action_items": [{{"task":"任务", "owner":"角色或spk=N", "deadline":"时间"}}],
      "risks_or_todos": ["风险/待确认"]
    }}
  ],
  "open_questions": ["会后待确认"],
  "consolidated_next_steps": ["最终行动项汇总（5-15 条）"],
  "one_sentence_summary": "一句话概括"
}}"""


def _llm_synthesize_summaries(
    summaries: list[str],
    original_prompt: str,
    *,
    base_url: str,
    api_key: str,
    model: str,
) -> dict:
    """用 LLM 做二次聚合：把各 chunk 摘要综合成一份完整纪要。失败返回 {}。"""
    try:
        # 把各段摘要解析后拼成 JSON 数组
        parsed_list = []
        failed_count = 0
        for s in summaries:
            parsed = _parse_json_response(s)
            if parsed is not None:
                parsed_list.append(parsed)
            else:
                failed_count += 1
        if failed_count:
            logger.warning(
                "_llm_synthesize_summaries: %d/%d 段摘要 JSON 解析失败，"
                "二次聚合将基于成功的 %d 段",
                failed_count, len(summaries), len(parsed_list),
            )
        if not parsed_list:
            return {}

        summaries_json = json.dumps(parsed_list, ensure_ascii=False, indent=2)
        # 环形截断：首尾各保留一半，中间省略。避免只砍尾部导致后半段关键内容丢失。
        _SYNTHESIZE_MAX = 20000
        if len(summaries_json) > _SYNTHESIZE_MAX:
            head = summaries_json[:_SYNTHESIZE_MAX // 2]
            tail = summaries_json[-_SYNTHESIZE_MAX // 2:]
            summaries_json = head + "\n... (中间 chunk 摘要省略)\n" + tail

        prompt = _SYNTHESIZE_PROMPT.format(summaries_json=summaries_json)
        result = call_llm(prompt, base_url=base_url, api_key=api_key, model=model)
        if not result:
            logger.warning("_llm_synthesize_summaries: call_llm 返回空")
            return {}
        parsed = _parse_json_response(result)
        if parsed is None:
            logger.warning("_llm_synthesize_summaries: 二次聚合返回的 JSON 解析失败")
            return {}
        # 验证结构合法性：至少要有 overall_summary 或 sections
        if parsed.get("overall_summary") or parsed.get("sections"):
            parsed["_synthesized"] = True
            return parsed
        return {}
    except Exception as e:
        logger.warning("二次聚合 LLM 调用失败：%s", e)
        return {}


# mindmap 二次聚合：让 LLM 综合多个 chunk 各自产出的子导图
_MINDMAP_SYNTHESIZE_PROMPT = """你现在的任务是**综合多个局部思维导图**，合并成一份完整的会议导图。

输入是同一场会议不同段落各自产出的思维导图 JSON 数组。请：
1. **去重合并**：相同或相近的议题合并为一条顶层 children
2. **控制规模**：顶层议题（children）不超过 8 个，深度不超过 3 层
3. **提炼概括**：保留关键要点，去掉细碎末节；节点 title 简洁有力

严格沿用下方的 JSON 结构，仅输出 JSON。

--- 原始子导图 JSON 数组 ---
{mindmaps_json}

--- 你需要输出的 JSON 结构 ---
{{
  "title": "会议主题（一句话）",
  "children": [
    {{
      "title": "议题名称（简洁）",
      "children": [
        {{ "title": "关键要点 1" }},
        {{ "title": "关键要点 2" }}
      ]
    }}
  ]
}}"""


def _llm_synthesize_mindmap(
    sub_roots: list[dict],
    *,
    base_url: str,
    api_key: str,
    model: str,
) -> dict:
    """用 LLM 做 mindmap 二次聚合。失败返回 {}。"""
    try:
        if not sub_roots:
            return {}
        mindmaps_json = json.dumps(sub_roots, ensure_ascii=False, indent=2)
        _MM_MAX = 20000
        if len(mindmaps_json) > _MM_MAX:
            head = mindmaps_json[:_MM_MAX // 2]
            tail = mindmaps_json[-_MM_MAX // 2:]
            mindmaps_json = head + "\n... (中间 chunk 导图省略)\n" + tail

        prompt = _MINDMAP_SYNTHESIZE_PROMPT.format(mindmaps_json=mindmaps_json)
        result = call_llm(prompt, base_url=base_url, api_key=api_key, model=model)
        if not result:
            logger.warning("_llm_synthesize_mindmap: call_llm 返回空")
            return {}
        parsed = _parse_json_response(result)
        if parsed is None:
            logger.warning("_llm_synthesize_mindmap: 二次聚合返回的 JSON 解析失败")
            return {}
        if parsed.get("title") or parsed.get("children"):
            parsed["_synthesized"] = True
            return parsed
        return {}
    except Exception as e:
        logger.warning("mindmap 二次聚合 LLM 调用失败：%s", e)
        return {}
