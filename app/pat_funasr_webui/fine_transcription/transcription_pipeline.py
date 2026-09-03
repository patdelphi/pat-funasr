# -*- coding: utf-8 -*-
"""
ASR + LLM 协同精细转录管线
串联：音频前处理 → ASR 转写(分块流式) → LLM 二次优化 → 纪要 → 思维导图

两种入口:
- run_pipeline()           : 一次性返回全部结果（兼容旧调用）
- run_pipeline_streaming() : 生成器，阶段性 yield 中间结果，用于 Gradio 实时展示
"""
import json
import logging
import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import requests

from .scene_templates import SceneTemplate, get_template
from .summary_processor import (
    call_llm,
    chunk_text,
    refine_transcript,
    refine_transcript_streaming,
    generate_summary,
    generate_mindmap,
    generate_summary_streaming,
    generate_mindmap_streaming,
)
from . import store

logger = logging.getLogger(__name__)

# 默认 ASR 服务地址
_ASR_BASE_URL = "http://127.0.0.1:8000"
# 默认 LLM 配置
_LLM_BASE_URL = "http://127.0.0.1:11434/v1"
_LLM_MODEL = "qwen2.5:7b"
_LLM_API_KEY = "no-key"

# 长音频分块阈值：超过此秒数按 CHUNK_SECONDS 切块后逐块 ASR，避免一次性推理被截断
_LONG_AUDIO_THRESHOLD_SECONDS = 300  # 5 分钟
_CHUNK_SECONDS = 240  # 每块 4 分钟
_CHUNK_OVERLAP_SECONDS = 10  # 块间重叠，避免边界语句丢失


# ------------------------------------------------------------------
# ffmpeg 音频探测 / 分块
# ------------------------------------------------------------------

def _probe_audio_duration(audio_path: str) -> float:
    """使用 ffprobe 获取音频时长(秒)，失败返回 0"""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                audio_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return float(result.stdout.strip() or 0)
    except Exception as e:
        logger.warning("ffprobe 时长探测失败: %s", e)
    return 0.0


def _split_audio_ffmpeg(
    audio_path: str,
    chunk_seconds: int = _CHUNK_SECONDS,
    overlap_seconds: int = _CHUNK_OVERLAP_SECONDS,
) -> List[Tuple[str, float]]:
    """
    使用 ffmpeg 将长音频切分为多段 wav
    返回 [(chunk_path, start_offset_seconds), ...]
    调用方负责删除临时目录
    """
    tmpdir = tempfile.mkdtemp(prefix="pat_funasr_chunks_")
    chunks: List[Tuple[str, float]] = []
    duration = _probe_audio_duration(audio_path)
    if duration <= 0:
        duration = 3600 * 10  # 探测失败，保守按 10 小时循环，直到 ffmpeg 切不出文件
    total = max(int(math.ceil(duration)), 1)
    start = 0
    idx = 0
    while start < total:
        out_path = os.path.join(tmpdir, f"chunk_{idx:04d}.wav")
        # 以 16kHz 16bit mono wav 输出，保证 ASR 输入格式一致
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", str(start),
            "-i", audio_path,
            "-t", str(chunk_seconds),
            "-ac", "1", "-ar", "16000", "-sample_fmt", "s16",
            out_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except Exception as e:
            logger.warning("chunk 切分失败 idx=%d start=%d: %s", idx, start, e)
            break
        # 文件存在且 > 4KB 才保留，否则认为到末尾了
        if result.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 4096:
            chunks.append((out_path, float(start)))
        else:
            break
        start += chunk_seconds - overlap_seconds
        idx += 1
        # 安全上限，防止异常死循环
        if idx > 500:
            break
    return chunks


def _merge_chunk_segments(
    all_segments: List[List[Dict]],
    chunk_offsets: List[float],
    overlap_seconds: int = _CHUNK_OVERLAP_SECONDS,
) -> List[Dict]:
    """
    合并多分块 segments，去除重叠区域的重复内容
    - 每块时间戳加上块起始偏移
    - 去重规则：若 (文本前缀, 近似时间戳) 指纹相同，且两者 start 间隔 < 2*overlap_seconds，则视为重复
    """
    if not all_segments:
        return []
    if len(all_segments) == 1:
        return all_segments[0]

    merged: List[Dict] = []
    # 指纹 -> 已添加段的 start 时间列表（可能相同文本在不同位置真实出现多次，只保留一定窗口去重）
    seen_map: Dict[Tuple, List[float]] = {}

    for seg_list, offset in zip(all_segments, chunk_offsets):
        for seg in seg_list:
            s = dict(seg)
            # 时间戳加上块偏移
            for key in ("start", "end"):
                if key in s and isinstance(s[key], (int, float)):
                    s[key] = float(s[key]) + offset
            # word 级时间戳同样偏移
            if isinstance(s.get("words"), list):
                new_words = []
                for w in s["words"]:
                    if isinstance(w, dict):
                        nw = dict(w)
                        for k in ("start", "end"):
                            if k in nw and isinstance(nw[k], (int, float)):
                                nw[k] = float(nw[k]) + offset
                        new_words.append(nw)
                    else:
                        new_words.append(w)
                s["words"] = new_words

            txt = (s.get("text") or "").strip()
            if not txt:
                continue
            start_sec = float(s.get("start") or 0)
            # 文本作为主指纹，时间窗口单独判断；若把整秒写入 key，轻微时间漂移会绕过去重。
            approx_key: Tuple = (txt[:40],)

            # 检查重复：若在近似窗口(2*overlap)内已存在相同指纹，则丢弃
            is_dup = False
            if approx_key in seen_map:
                for prev_start in seen_map[approx_key]:
                    # 去重窗口严格限制为 2×overlap，避免误删远距离的真重复
                    # （如"对对对""是的"等口语在 30 秒内多次出现）
                    if abs(start_sec - prev_start) <= overlap_seconds * 2:
                        is_dup = True
                        break
            if is_dup:
                continue
            seen_map.setdefault(approx_key, []).append(start_sec)
            merged.append(s)

    merged.sort(key=lambda x: float(x.get("start") or 0))
    return merged


# ------------------------------------------------------------------
# ASR 调用（单文件）
# ------------------------------------------------------------------

def _call_asr(
    audio_path: str,
    model: str = "sensevoice",
    template: SceneTemplate = None,
    hotwords: list = None,
    base_url: str = _ASR_BASE_URL,
) -> dict:
    """
    调用 ASR 服务。

    普通场景调用 OpenAI-compatible 转写端点；场景显式启用 diarization 时，
    调用说话人分离端点并直接返回带 speaker 的 segments。
    """
    try:
        asr_params = dict(template.asr_params) if template and template.asr_params else {}
        use_diarization = bool(asr_params.pop("diarization", False))
        if use_diarization:
            endpoint = "/v1/funasr/diarization"
            data = {
                "model": model,
                "spk_model": str(asr_params.pop("spk_model", "cam++")),
                "spk_mode": str(asr_params.pop("spk_mode", "punc_segment")),
            }
            preset_spk_num = asr_params.pop("preset_spk_num", None)
            if preset_spk_num not in {None, "", 0, "0"}:
                data["preset_spk_num"] = str(preset_spk_num)
        else:
            endpoint = "/v1/audio/transcriptions"
            data = {
                "model": model,
                "response_format": "verbose_json",
            }
            for key, value in asr_params.items():
                if key not in data:
                    data[key] = str(value)
            if hotwords:
                data["hotword"] = ",".join(hotwords)

        # 请求结束后立即关闭文件句柄，避免长任务和批处理累积占用。
        with open(audio_path, "rb") as audio_stream:
            files = {"file": (Path(audio_path).name, audio_stream, "audio/wav")}
            resp = requests.post(
                f"{base_url}{endpoint}",
                files=files,
                data=data,
                timeout=1800,  # 30 分钟，长音频首次加载模型可能较慢
            )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        raise RuntimeError("ASR 调用超时")
    except requests.exceptions.ConnectionError:
        raise RuntimeError(f"无法连接 ASR 服务({base_url})，请确认服务已启动")
    except Exception as e:
        raise RuntimeError(f"ASR 调用失败: {e}")


def _call_asr_chunked(
    audio_path: str,
    model: str = "sensevoice",
    template: SceneTemplate = None,
    hotwords: list = None,
    base_url: str = _ASR_BASE_URL,
    chunk_callback=None,
) -> Dict:
    """
    长音频分块 ASR：
    - 短于 _LONG_AUDIO_THRESHOLD_SECONDS 直接调用
    - 超过则按 _CHUNK_SECONDS 切块，逐块推理并合并
    chunk_callback(chunk_idx, total_chunks, partial_result) 用于流式展示
    """
    duration = _probe_audio_duration(audio_path)
    if duration > 0 and duration <= _LONG_AUDIO_THRESHOLD_SECONDS:
        # 短音频直接走单次调用
        result = _call_asr(audio_path, model, template, hotwords, base_url)
        if chunk_callback:
            seg = result.get("segments") or []
            partial = {
                "segments": seg,
                "text": result.get("text", ""),
            }
            chunk_callback(0, 1, partial)
        return result

    # 长音频：切分 → 逐块转写 → 合并
    chunks = _split_audio_ffmpeg(audio_path)
    if not chunks:
        # 切分失败，回退到单次调用
        logger.warning("长音频切分失败，回退到单次 ASR")
        return _call_asr(audio_path, model, template, hotwords, base_url)

    all_segments: List[List[Dict]] = []
    chunk_offsets: List[float] = []
    all_raw_texts: List[str] = []
    total_duration = 0.0
    tmpdirs = set()

    try:
        for i, (chunk_path, offset) in enumerate(chunks):
            if os.path.isdir(os.path.dirname(chunk_path)):
                tmpdirs.add(os.path.dirname(chunk_path))
            chunk_result = _call_asr(chunk_path, model, template, hotwords, base_url)
            seg = chunk_result.get("segments") or []
            txt = chunk_result.get("text", "") or ""
            all_segments.append(seg)
            chunk_offsets.append(offset)
            all_raw_texts.append(txt)
            total_duration = max(total_duration, float(chunk_result.get("duration") or 0) + offset)
            # 流式回调：当前块 + 合并后的已有结果
            if chunk_callback:
                merged_so_far = _merge_chunk_segments(
                    all_segments, chunk_offsets,
                )
                partial = {
                    "segments": merged_so_far,
                    "text": "".join(str(item.get("text") or "") for item in merged_so_far),
                }
                chunk_callback(i, len(chunks), partial)
    finally:
        # 清理临时 chunk 文件
        for d in tmpdirs:
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass

    merged_segments = _merge_chunk_segments(all_segments, chunk_offsets)
    return {
        "segments": merged_segments,
        "text": "".join(str(item.get("text") or "") for item in merged_segments),
        "duration": total_duration,
        "chunked": True,
        "chunks": len(chunks),
    }


# ------------------------------------------------------------------
# Pipeline 内部步骤（供流式/非流式复用）
# ------------------------------------------------------------------

def _prepare_hotwords(template: SceneTemplate, custom_hotwords: str) -> List[str]:
    hotwords = list(template.hotwords)
    if custom_hotwords.strip():
        hotwords += [w.strip() for w in custom_hotwords.strip().split("\n") if w.strip()]
    return hotwords


def _preprocess_audio_if_needed(audio_path, enable_preprocess, denoise_strength, enable_vad):
    processed_audio = audio_path
    if enable_preprocess:
        from . import audio_processor

        processed_audio, _info_before, _info_after = audio_processor.process_audio(
            audio_path,
            noise_reduction=True,
            noise_strength=denoise_strength,
            sample_rate=16000,
            vad_enabled=enable_vad,
            loudnorm=True,
        )
    return processed_audio


def _save_to_store(
    task_id_ref,
    scene_id, template, audio_path, asr_result, model,
    refined_text, summary_result, mindmap_result, llm_model,
):
    try:
        store.init_db()
        task_id = store.create_task(
            scene_id=scene_id,
            scene_name=template.name,
            audio_file=audio_path,
            audio_duration=asr_result.get("duration", 0),
            asr_model=model,
        )
        store.save_segments(task_id, asr_result.get("segments") or [])
        if refined_text:
            store.save_llm_output(task_id, "refined_transcript", refined_text, llm_model)
        if summary_result:
            store.save_llm_output(task_id, "summary", json.dumps(summary_result, ensure_ascii=False), llm_model)
        if mindmap_result:
            store.save_llm_output(task_id, "mindmap", json.dumps(mindmap_result, ensure_ascii=False), llm_model)
        store.update_task_status(task_id, "completed")
        task_id_ref[0] = task_id
    except Exception as e:
        logger.warning("存储失败(不影响结果): %s", e)
        task_id_ref[0] = None


# ------------------------------------------------------------------
# 公开 API：一次性全量 pipeline（保持向后兼容）
# ------------------------------------------------------------------

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
    custom_prompt: str = None,
    progress_callback=None,
) -> dict:
    """
    执行精细转录全链路（一次性返回）
    返回 {segments, raw_text, refined_text, summary, mindmap, audio_path, elapsed, task_id, scene_name}
    """
    # 复用流式实现的逻辑，只是收集最后一次 yield 的 final 结果
    final_result = None
    for stage, payload in run_pipeline_streaming(
        audio_path=audio_path,
        scene_id=scene_id,
        model=model,
        enable_preprocess=enable_preprocess,
        denoise_strength=denoise_strength,
        enable_vad=enable_vad,
        enable_llm_refine=enable_llm_refine,
        enable_summary=enable_summary,
        enable_mindmap=enable_mindmap,
        custom_hotwords=custom_hotwords,
        asr_base_url=asr_base_url,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
        custom_prompt=custom_prompt,
    ):
        if stage == "final":
            final_result = payload
        elif stage == "progress" and progress_callback:
            progress_callback(payload.get("progress", 0), desc=payload.get("desc", ""))
    if final_result is None:
        raise RuntimeError("pipeline 未产生结果")
    return final_result


# ------------------------------------------------------------------
# 公开 API：流式 pipeline（生成器）
# ------------------------------------------------------------------

def run_pipeline_streaming(
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
    custom_prompt: str = None,
) -> Iterator[Tuple[str, Dict]]:
    """
    流式执行精细转录，生成器 yield:
        ("asr_chunk",    partial_dict)      : 每块 ASR 结束（长音频会有多次），partial 即截至当前块的合并结果
        ("progress",     {progress, desc})  : 进度通知 (0~1)
        ("llm_refine",   {text, refined})   : LLM 二次优化阶段，按文本块流式回传（此处为一次性最终）
        ("summary",      summary_result)    : 纪要生成完成
        ("mindmap",      mindmap_result)    : 思维导图生成完成
        ("final",        full_result)       : 最终完整结果
        ("error",        {message})         : 异常
    """
    t0 = time.time()
    template = get_template(scene_id) or get_template("general")
    hotwords = _prepare_hotwords(template, custom_hotwords)
    active_prompt = custom_prompt if custom_prompt and custom_prompt.strip() else template.llm_prompt

    processed_audio = audio_path
    task_id_ref: List[Optional[int]] = [None]

    try:
        # ---- 步骤 1: 音频前处理 ----
        yield "progress", {"progress": 0.05, "desc": "准备音频..."}
        processed_audio = _preprocess_audio_if_needed(
            audio_path, enable_preprocess, denoise_strength, enable_vad,
        )

        # ---- 步骤 2: ASR 分块转写 + 流式回传 ----
        yield "progress", {"progress": 0.1, "desc": "ASR 转写中(0/?)..."}

        # 用闭包 + 收集器实现：每个 chunk 回调把事件入队，外层 yield 消费
        asr_partial_collector: List[Tuple[str, Dict]] = []

        def _chunk_collector(chunk_idx, total, partial):
            prog = 0.1 + (min(chunk_idx + 1, total) / max(total, 1)) * 0.35
            desc = f"ASR 转写中({chunk_idx + 1}/{total})..."
            asr_partial_collector.append(("progress", {"progress": prog, "desc": desc}))
            asr_partial_collector.append(("asr_chunk", partial))

        asr_result = _call_asr_chunked(
            processed_audio,
            model=model,
            template=template,
            hotwords=hotwords,
            base_url=asr_base_url,
            chunk_callback=_chunk_collector,
        )
        # 把 ASR 期间缓存的中间事件全部发出
        for evt in asr_partial_collector:
            yield evt

        segments = asr_result.get("segments", [])
        raw_text = asr_result.get("text", "")
        if not raw_text:
            raise RuntimeError("ASR 转写结果为空")
        # 若单次 ASR 未触发 chunk（短音频），这里也需要补一次 asr_chunk 事件，让前端能显示
        if not asr_partial_collector:
            yield "asr_chunk", {"segments": segments, "text": raw_text}

        # ---- 步骤 3: LLM 二次优化（分块流式，每块前/后推进度，失败推 warning） ----
        refined_text = ""
        if enable_llm_refine and active_prompt:
            yield "progress", {"progress": 0.50, "desc": "准备 LLM 二次优化..."}
            accumulated = ""
            for evt, data in refine_transcript_streaming(
                raw_text,
                active_prompt,
                hotwords=hotwords,
                base_url=llm_base_url,
                api_key=llm_api_key,
                model=llm_model,
            ):
                if evt == "chunk_start":
                    idx = data["idx"]; tot = data["total"]
                    p = 0.50 + 0.18 * (idx - 1) / max(1, tot)
                    yield "progress", {
                        "progress": p,
                        "desc": f"🧠 LLM 优化 第 {idx}/{tot} 块中...",
                    }
                elif evt == "piece_done":
                    idx = data["idx"]; tot = data["total"]
                    ok = data.get("success", False)
                    accumulated = data.get("text_so_far") or ""
                    yield "llm_refine", {"text": accumulated, "final": False}
                    p = 0.50 + 0.18 * idx / max(1, tot)
                    if ok:
                        yield "progress", {
                            "progress": p,
                            "desc": f"🧠 LLM 优化 第 {idx}/{tot} 块完成",
                        }
                    else:
                        warn = (f"LLM 优化第 {idx}/{tot} 块未返回内容（可能触发熔断或超时），"
                                f"将保留原始 ASR 文本对应片段")
                        logger.warning(warn)
                        yield "warning", {"message": warn, "stage": "llm_refine",
                                          "chunk": idx, "total": tot}
                        yield "progress", {
                            "progress": p,
                            "desc": f"🧠 LLM 优化 第 {idx}/{tot} 块跳过(空结果)",
                        }
                elif evt == "done":
                    refined_text = data or ""
                    if refined_text:
                        yield "llm_refine", {"text": refined_text, "final": True}

        # ---- 步骤 4: 纪要生成（分块流式） ----
        summary_result = {}
        if enable_summary and template.summary_prompt:
            yield "progress", {"progress": 0.70, "desc": "准备生成纪要..."}
            text_for_summary = refined_text or raw_text
            for evt, data in generate_summary_streaming(
                text_for_summary,
                template.summary_prompt,
                base_url=llm_base_url,
                api_key=llm_api_key,
                model=llm_model,
            ):
                if evt == "chunk_start":
                    idx = data["idx"]; tot = data["total"]
                    p = 0.70 + 0.12 * (idx - 1) / max(1, tot)
                    yield "progress", {
                        "progress": p,
                        "desc": f"📝 纪要生成 第 {idx}/{tot} 块中...",
                    }
                elif evt == "chunk_done":
                    idx = data["idx"]; tot = data["total"]
                    ok = data.get("success", False)
                    interim = data.get("summary_so_far") or {}
                    if interim:
                        yield "summary", interim
                    p = 0.70 + 0.12 * idx / max(1, tot)
                    if ok:
                        yield "progress", {"progress": p,
                                           "desc": f"📝 纪要 第 {idx}/{tot} 块完成"}
                    else:
                        warn = f"纪要第 {idx}/{tot} 块未返回内容（可能熔断/超时）"
                        logger.warning(warn)
                        yield "warning", {"message": warn, "stage": "summary",
                                          "chunk": idx, "total": tot}
                        yield "progress", {"progress": p,
                                           "desc": f"📝 纪要 第 {idx}/{tot} 块跳过"}
                elif evt == "agg_start":
                    yield "progress", {"progress": 0.82, "desc": "📝 纪要多段聚合中..."}
                elif evt == "done":
                    summary_result = data or {}
                    if summary_result:
                        yield "summary", summary_result
                    yield "progress", {"progress": 0.83, "desc": "📝 纪要生成完成"}

        # ---- 步骤 5: 思维导图（分块流式，全量文本聚合） ----
        mindmap_result = {}
        if enable_mindmap and template.mindmap_prompt:
            yield "progress", {"progress": 0.84, "desc": "准备生成思维导图..."}
            text_for_mindmap = refined_text or raw_text
            for evt, data in generate_mindmap_streaming(
                text_for_mindmap,
                template.mindmap_prompt,
                base_url=llm_base_url,
                api_key=llm_api_key,
                model=llm_model,
            ):
                if evt == "chunk_start":
                    idx = data["idx"]; tot = data["total"]
                    p = 0.84 + 0.10 * (idx - 1) / max(1, tot)
                    yield "progress", {
                        "progress": p,
                        "desc": f"🗺️ 思维导图 第 {idx}/{tot} 块中...",
                    }
                elif evt == "chunk_done":
                    idx = data["idx"]; tot = data["total"]
                    ok = data.get("success", False)
                    interim = data.get("mindmap_so_far") or {}
                    if interim:
                        yield "mindmap", interim
                    p = 0.84 + 0.10 * idx / max(1, tot)
                    if ok:
                        yield "progress", {"progress": p,
                                           "desc": f"🗺️ 思维导图 第 {idx}/{tot} 块完成"}
                    else:
                        warn = f"思维导图第 {idx}/{tot} 块未返回内容（可能熔断/超时）"
                        logger.warning(warn)
                        yield "warning", {"message": warn, "stage": "mindmap",
                                          "chunk": idx, "total": tot}
                        yield "progress", {"progress": p,
                                           "desc": f"🗺️ 思维导图 第 {idx}/{tot} 块跳过"}
                elif evt == "agg_start":
                    yield "progress", {"progress": 0.94, "desc": "🗺️ 多段子图聚合中..."}
                elif evt == "done":
                    mindmap_result = data or {}
                    if mindmap_result:
                        yield "mindmap", mindmap_result
                    yield "progress", {"progress": 0.94, "desc": "🗺️ 思维导图完成"}

        elapsed = time.time() - t0

        # ---- 步骤 6: 存储 ----
        yield "progress", {"progress": 0.95, "desc": "保存结果..."}
        _save_to_store(
            task_id_ref, scene_id, template, audio_path, asr_result, model,
            refined_text, summary_result, mindmap_result, llm_model,
        )

        yield "progress", {"progress": 1.0, "desc": "完成!"}

        yield "final", {
            "segments": segments,
            "raw_text": raw_text,
            "refined_text": refined_text,
            "summary": summary_result,
            "mindmap": mindmap_result,
            "audio_path": processed_audio,
            "elapsed": elapsed,
            "task_id": task_id_ref[0],
            "scene_name": template.name,
        }

    except Exception as e:
        logger.exception("pipeline 执行异常")
        yield "error", {"message": str(e)}


# ------------------------------------------------------------------
# 公共工具：JSON 兜底解析（供外部需要时复用，内部流式模块已内置对应逻辑）
# ------------------------------------------------------------------


def _parse_json_safe(text: str) -> dict:
    """从 LLM 输出中提取 JSON，兼容 markdown 代码块"""
    if not text:
        return {}
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        # 失败时构造一个包含纯文本的兜底节点
        return {"title": "思维导图", "children": [{"title": line[:100]} for line in text.splitlines() if line.strip()]}


# ------------------------------------------------------------------
# 结果格式化与导出（保持旧 API 被 gradio_app.py 调用）
# ------------------------------------------------------------------

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
