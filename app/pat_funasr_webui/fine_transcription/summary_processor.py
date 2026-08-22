# -*- coding: utf-8 -*-
"""
纪要+思维导图 LLM 生成模块
使用 requests 调用 OpenAI 兼容接口(支持 Ollama/OpenAI/Claude)
"""
import json
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# 默认 LLM 配置
_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"  # Ollama 默认地址
_DEFAULT_MODEL = "qwen2.5:7b"
_DEFAULT_TIMEOUT = 300  # 5 分钟超时


def call_llm(
    prompt: str,
    system_prompt: str = "",
    base_url: str = _DEFAULT_BASE_URL,
    api_key: str = "no-key",
    model: str = _DEFAULT_MODEL,
    temperature: float = 0.3,
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    """
    调用 LLM（OpenAI 兼容接口）
    返回模型输出文本，异常时返回空字符串
    """
    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
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
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    except requests.exceptions.Timeout:
        logger.error("LLM 调用超时(%ds)", timeout)
        return ""
    except Exception as e:
        logger.error("LLM 调用失败: %s", e)
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
    生成结构化纪要
    返回 JSON dict，解析失败返回空 dict
    """
    if not summary_prompt:
        return {}

    chunks = chunk_text(transcript_text)
    summaries = []
    for i, chunk in enumerate(chunks):
        prompt = f"{summary_prompt}\n\n--- 转写文本(第{i+1}/{len(chunks)}段) ---\n{chunk}"
        result = call_llm(prompt, base_url=base_url, api_key=api_key, model=model)
        if result:
            summaries.append(result)

    # 如果只有一段，直接解析
    if len(summaries) == 1:
        return _parse_json_response(summaries[0])

    # 多段聚合：合并为一个 JSON
    return _aggregate_summaries(summaries)


def generate_mindmap(
    transcript_text: str,
    mindmap_prompt: str,
    base_url: str = _DEFAULT_BASE_URL,
    api_key: str = "no-key",
    model: str = _DEFAULT_MODEL,
) -> dict:
    """
    生成思维导图 JSON 树
    返回 {title, children:[...]} 格式 dict
    """
    if not mindmap_prompt:
        return {}

    chunks = chunk_text(transcript_text)
    # 只取前 2 段做思维导图(足够覆盖主要内容)
    combined = "\n".join(chunks[:2])
    prompt = f"{mindmap_prompt}\n\n--- 转写文本 ---\n{combined}"
    result = call_llm(prompt, base_url=base_url, api_key=api_key, model=model)
    if not result:
        return {}

    return _parse_json_response(result)


def refine_transcript(
    asr_text: str,
    llm_prompt: str,
    hotwords: list = None,
    base_url: str = _DEFAULT_BASE_URL,
    api_key: str = "no-key",
    model: str = _DEFAULT_MODEL,
) -> str:
    """
    LLM 二次优化转写文本
    返回润色后的纯文本
    """
    hotword_str = "、".join(hotwords) if hotwords else "无"
    prompt = f"""{llm_prompt}

--- 专业词表 ---
{hotword_str}

--- ASR 原始转写 ---
{asr_text}
"""
    return call_llm(prompt, base_url=base_url, api_key=api_key, model=model)


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
