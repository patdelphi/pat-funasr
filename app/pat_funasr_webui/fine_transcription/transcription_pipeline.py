# -*- coding: utf-8 -*-
"""
ASR + LLM 协同精细转录管线
串联：音频前处理 → ASR 转写 → LLM 二次优化 → 纪要 → 思维导图
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests

from .scene_templates import SceneTemplate, get_template
from .summary_processor import (
    call_llm, refine_transcript, generate_summary, generate_mindmap
)
from . import store

logger = logging.getLogger(__name__)

# 默认 ASR 服务地址
_ASR_BASE_URL = "http://127.0.0.1:8000"
# 默认 LLM 配置
_LLM_BASE_URL = "http://127.0.0.1:11434/v1"
_LLM_MODEL = "qwen2.5:7b"
_LLM_API_KEY = "no-key"


def run_pipeline(
    audio_path: str,
    scene_id: str = "general",
    model: str = "sensevoice",
    enable_preprocess: bool = False,
    denoise_strength: int = 0,
    enable_vad: bool = False,
    enable_llm_refine: bool = True,
    enable_summary: bool = True,
    enable_mindmap: bool = True,
    custom_hotwords: str = "",
    asr_base_url: str = _ASR_BASE_URL,
    llm_base_url: str = _LLM_BASE_URL,
    llm_model: str = _LLM_MODEL,
    llm_api_key: str = _LLM_API_KEY,
    progress_callback=None,
) -> dict:
    """
    执行精细转录全链路
    返回 {segments, raw_text, refined_text, summary, mindmap, audio_path, elapsed}
    """
    t0 = time.time()
    template = get_template(scene_id) or get_template("general")

    # 合并词表
    hotwords = list(template.hotwords)
    if custom_hotwords.strip():
        hotwords += [w.strip() for w in custom_hotwords.strip().split("\n") if w.strip()]

    # 步骤 1: 音频前处理（可选）
    processed_audio = audio_path
    if enable_preprocess:
        if progress_callback:
            progress_callback(0.1, desc="音频前处理中...")
        try:
            from .audio_processor import preprocess_audio
            processed_audio = preprocess_audio(
                audio_path,
                denoise_strength=denoise_strength,
                enable_vad=enable_vad,
                output_suffix="_preprocessed",
            )
        except Exception as e:
            logger.warning("前处理失败，使用原始音频: %s", e)

    # 步骤 2: ASR 转写
    if progress_callback:
        progress_callback(0.2, desc="ASR 转写中...")
    asr_result = _call_asr(
        processed_audio,
        model=model,
        template=template,
        hotwords=hotwords,
        base_url=asr_base_url,
    )

    segments = asr_result.get("segments", [])
    raw_text = asr_result.get("text", "")

    if not raw_text:
        raise RuntimeError("ASR 转写结果为空")

    # 步骤 3: LLM 二次优化
    refined_text = ""
    if enable_llm_refine and template.llm_prompt:
        if progress_callback:
            progress_callback(0.4, desc="LLM 二次优化中...")
        refined_text = refine_transcript(
            raw_text,
            template.llm_prompt,
            hotwords=hotwords,
            base_url=llm_base_url,
            api_key=llm_api_key,
            model=llm_model,
        )

    # 步骤 4: 纪要生成
    summary_result = {}
    if enable_summary and template.summary_prompt:
        if progress_callback:
            progress_callback(0.6, desc="生成会议纪要中...")
        text_for_summary = refined_text or raw_text
        summary_result = generate_summary(
            text_for_summary,
            template.summary_prompt,
            base_url=llm_base_url,
            api_key=llm_api_key,
            model=llm_model,
        )

    # 步骤 5: 思维导图
    mindmap_result = {}
    if enable_mindmap and template.mindmap_prompt:
        if progress_callback:
            progress_callback(0.8, desc="生成思维导图中...")
        text_for_mindmap = refined_text or raw_text
        mindmap_result = generate_mindmap(
            text_for_mindmap,
            template.mindmap_prompt,
            base_url=llm_base_url,
            api_key=llm_api_key,
            model=llm_model,
        )

    elapsed = time.time() - t0

    # 步骤 6: 存入 SQLite
    try:
        store.init_db()
        task_id = store.create_task(
            scene_id=scene_id,
            scene_name=template.name,
            audio_file=audio_path,
            audio_duration=asr_result.get("duration", 0),
            asr_model=model,
        )
        store.save_segments(task_id, segments)
        if refined_text:
            store.save_llm_output(task_id, "refined_transcript", refined_text, llm_model)
        if summary_result:
            store.save_llm_output(task_id, "summary", json.dumps(summary_result, ensure_ascii=False), llm_model)
        if mindmap_result:
            store.save_llm_output(task_id, "mindmap", json.dumps(mindmap_result, ensure_ascii=False), llm_model)
        store.update_task_status(task_id, "completed")
    except Exception as e:
        logger.warning("存储失败(不影响结果): %s", e)
        task_id = None

    if progress_callback:
        progress_callback(1.0, desc="完成!")

    return {
        "segments": segments,
        "raw_text": raw_text,
        "refined_text": refined_text,
        "summary": summary_result,
        "mindmap": mindmap_result,
        "audio_path": processed_audio,
        "elapsed": elapsed,
        "task_id": task_id,
        "scene_name": template.name,
    }


def _call_asr(
    audio_path: str,
    model: str = "sensevoice",
    template: SceneTemplate = None,
    hotwords: list = None,
    base_url: str = _ASR_BASE_URL,
) -> dict:
    """
    调用 ASR 服务 /v1/audio/transcriptions
    返回 verbose_json 格式结果
    """
    try:
        # 构建 multipart form data
        files = {"file": (Path(audio_path).name, open(audio_path, "rb"), "audio/wav")}
        data = {
            "model": model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "word",
        }
        # 场景参数覆盖
        if template and template.asr_params:
            for k, v in template.asr_params.items():
                if k not in data:
                    data[k] = str(v)
        # 热词
        if hotwords:
            data["hotword"] = ",".join(hotwords)

        resp = requests.post(
            f"{base_url}/v1/audio/transcriptions",
            files=files,
            data=data,
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        raise RuntimeError("ASR 调用超时")
    except requests.exceptions.ConnectionError:
        raise RuntimeError(f"无法连接 ASR 服务({base_url})，请确认服务已启动")
    except Exception as e:
        raise RuntimeError(f"ASR 调用失败: {e}")


def format_transcript_text(segments: list, refined_text: str = "") -> str:
    """格式化转写文本用于显示"""
    if refined_text:
        return refined_text

    lines = []
    for seg in segments:
        speaker = seg.get("speaker", "")
        text = seg.get("text", "").strip()
        start = seg.get("start", 0)
        mm = int(start // 60)
        ss = int(start % 60)
        timestamp = f"[{mm:02d}:{ss:02d}]"
        if speaker:
            lines.append(f"{timestamp} {speaker}: {text}")
        else:
            lines.append(f"{timestamp} {text}")
    return "\n".join(lines)


def format_summary_display(summary: dict) -> str:
    """格式化纪要 JSON 为可读文本"""
    if not summary:
        return "（未生成纪要）"

    if summary.get("aggregated"):
        parts = summary.get("parts", [])
        lines = [f"### 纪要（{len(parts)} 段聚合）\n"]
        for i, part in enumerate(parts):
            lines.append(f"**第 {i+1} 段:**")
            for k, v in part.items():
                if isinstance(v, list):
                    lines.append(f"- {k}: {', '.join(str(x) for x in v)}")
                else:
                    lines.append(f"- {k}: {v}")
            lines.append("")
        return "\n".join(lines)

    lines = ["### 纪要\n"]
    field_labels = {
        "participants": "参会人员",
        "summary": "概要",
        "deadlines": "截止日期",
        "decisions": "决策",
        "action_items": "行动项",
        "follow_ups": "后续跟进",
        "notes": "备注",
        "interviewee": "受访者",
        "topics": "讨论主题",
        "key_insights": "关键洞察",
        "pain_points": "痛点",
        "suggestions": "建议",
        "quotes": "重要引用",
        "conclusion": "结论",
        "case_type": "案件类型",
        "parties": "当事人",
        "claims": "诉讼请求",
        "evidence": "证据",
        "key_points": "争议焦点",
        "rulings": "裁定",
        "timeline": "时间线",
        "subjective": "主诉",
        "objective": "检查",
        "assessment": "诊断",
        "plan": "治疗方案",
        "medications": "用药",
        "follow_up": "随访",
        "warnings": "注意事项",
        "topic": "主题",
        "key_points": "核心知识点",
        "formulas": "公式",
        "examples": "例题",
        "homework": "作业",
        "references": "参考资料",
    }
    for k, v in summary.items():
        label = field_labels.get(k, k)
        if isinstance(v, list):
            lines.append(f"**{label}:**")
            for item in v:
                lines.append(f"  - {item}")
        elif isinstance(v, dict):
            lines.append(f"**{label}:**")
            for dk, dv in v.items():
                lines.append(f"  - {dk}: {dv}")
        else:
            lines.append(f"**{label}:** {v}")
    return "\n".join(lines)


def export_result(result: dict, format: str = "md") -> str:
    """
    导出精细转录结果
    format: txt / md / json
    """
    if format == "json":
        return json.dumps(result, ensure_ascii=False, indent=2)

    segments = result.get("segments", [])
    raw_text = result.get("raw_text", "")
    refined_text = result.get("refined_text", "")
    summary = result.get("summary", {})
    mindmap = result.get("mindmap", {})
    scene_name = result.get("scene_name", "")
    elapsed = result.get("elapsed", 0)

    if format == "txt":
        lines = [f"精细转录结果 - {scene_name}", f"耗时: {elapsed:.1f}s", "=" * 60, ""]
        if refined_text:
            lines.append("【优化后文本】")
            lines.append(refined_text)
        else:
            lines.append("【原始转写】")
            lines.append(raw_text)
        if summary:
            lines.append("")
            lines.append("=" * 60)
            lines.append("【纪要】")
            lines.append(json.dumps(summary, ensure_ascii=False, indent=2))
        if mindmap:
            lines.append("")
            lines.append("=" * 60)
            lines.append("【思维导图】")
            lines.append(json.dumps(mindmap, ensure_ascii=False, indent=2))
        return "\n".join(lines)

    # markdown
    lines = [f"# 精细转录结果 - {scene_name}", "", f"> 耗时: {elapsed:.1f}s", ""]
    lines.append("## 转写文本")
    if refined_text:
        lines.append(refined_text)
    else:
        lines.append(raw_text)

    if summary:
        lines.append("")
        lines.append("## 纪要")
        lines.append(format_summary_display(summary))

    if mindmap:
        lines.append("")
        lines.append("## 思维导图")
        lines.append("```json")
        lines.append(json.dumps(mindmap, ensure_ascii=False, indent=2))
        lines.append("```")

    return "\n".join(lines)
