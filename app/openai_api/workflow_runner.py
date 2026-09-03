"""
程序说明：
执行精细转录显式工作流，串联前处理、多模型 ASR、说话人对齐、校对和导出。

模型调用通过 WorkflowRuntime 注入，使 API、测试与后续其他前端复用同一编排逻辑。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import alignment_service
import artifact_service
import reconciliation_service
from workflow_service import ModelRunConfig, WorkflowConfig, WorkflowRunContext, parse_workflow_config


TranscribeFn = Callable[[str, ModelRunConfig, WorkflowConfig, Callable[[int, int, str], None]], dict[str, Any]]


@dataclass
class WorkflowRuntime:
    """工作流运行时依赖；只有启用的阶段才要求对应回调存在。"""

    transcribe: TranscribeFn
    preprocess: Callable[[str, Any, str], str] | None = None
    diarize: Callable[[str, Any], dict[str, Any]] | None = None
    llm_stage: Callable[[str, str, Any], Any] | None = None
    translate: Callable[[str, Any], Any] | None = None
    emotion: Callable[[str, Any], Any] | None = None
    write_artifacts: Callable[..., list[dict[str, Any]]] | None = None


def _emit_stage(
    context: WorkflowRunContext,
    *,
    stage: str,
    progress: float,
    message: str,
    level: str = "progress",
    model: str = "",
    **extra: Any,
) -> None:
    context.emit(
        level=level,
        stage=stage,
        progress=progress,
        message=message,
        model=model,
        **extra,
    )


def _run_one_model(
    context: WorkflowRunContext,
    runtime: WorkflowRuntime,
    source_path: str,
    config: WorkflowConfig,
    model_config: ModelRunConfig,
    stage: str,
    progress_start: float,
    progress_end: float,
) -> dict[str, Any]:
    context.raise_if_cancelled()
    _emit_stage(
        context,
        stage=stage,
        progress=progress_start,
        message=f"模型 {model_config.model} 开始转录",
        model=model_config.model,
    )

    def on_progress(current: int, total: int, message: str) -> None:
        context.raise_if_cancelled()
        ratio = current / max(1, total)
        _emit_stage(
            context,
            stage=stage,
            progress=progress_start + (progress_end - progress_start) * ratio,
            message=message,
            model=model_config.model,
            current=current,
            total=total,
        )

    result = runtime.transcribe(source_path, model_config, config, on_progress)
    result.setdefault("model", model_config.model)
    result.setdefault("weight", model_config.weight)
    result.setdefault("segments", [])
    result.setdefault("text", "".join(str(item.get("text") or "") for item in result["segments"]))
    _emit_stage(
        context,
        stage=stage,
        progress=progress_end,
        message=f"模型 {model_config.model} 转录完成",
        level="success",
        model=model_config.model,
    )
    return result


def _run_transcriptions(
    context: WorkflowRunContext,
    runtime: WorkflowRuntime,
    source_path: str,
    config: WorkflowConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    transcription = config.transcription

    # 默认 execution=parallel 且有 reviewers 时，primary + reviewers 一起并行
    if (
        transcription.mode == "multi_model"
        and transcription.reviewers
        and transcription.execution == "parallel"
    ):
        all_models = [transcription.primary, *transcription.reviewers]
        workers = min(transcription.max_concurrency or 2, len(all_models))

        # 并行执行，按原始索引收集结果
        indexed_results: dict[int, dict[str, Any] | None] = {}
        failed_reviewers: list[tuple[int, Exception]] = []

        def _submit_model(item: ModelRunConfig, stage: str, p_start: float, p_end: float) -> dict[str, Any]:
            return _run_one_model(context, runtime, source_path, config, item, stage, p_start, p_end)

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pat-asr") as executor:
            futures = {
                executor.submit(
                    _submit_model,
                    item,
                    "transcription.primary" if i == 0 else "transcription.reviewers",
                    0.12,
                    0.58,
                ): i
                for i, item in enumerate(all_models)
            }
            for future in as_completed(futures):
                idx = futures[future]
                item = all_models[idx]
                try:
                    indexed_results[idx] = future.result()
                except Exception as exc:
                    if item is transcription.primary:
                        raise  # primary 失败直接抛出
                    # reviewer 失败，先记录
                    if transcription.resource_failure_policy == "skip_failed_reviewer":
                        _emit_stage(
                            context,
                            stage="transcription.reviewers",
                            progress=0.58,
                            message=f"校对模型 {item.model} 失败，已按策略跳过：{exc}",
                            level="warning",
                            model=item.model,
                            error_code="REVIEWER_SKIPPED",
                            retryable=True,
                        )
                        indexed_results[idx] = None
                    elif transcription.resource_failure_policy == "fallback_to_serial":
                        failed_reviewers.append((idx, exc))
                    else:
                        raise

        # fallback_to_serial: 失败的 reviewer 串行重试
        for idx, exc in failed_reviewers:
            item = all_models[idx]
            _emit_stage(
                context,
                stage="transcription.reviewers",
                progress=0.58,
                message=f"并行 {item.model} 失败，回退串行重试：{exc}",
                level="warning",
                model=item.model,
                error_code="REVIEWER_SERIAL_RETRY",
                retryable=True,
            )
            result = _run_one_model(
                context, runtime, source_path, config, item,
                "transcription.reviewers", 0.12, 0.58,
            )
            indexed_results[idx] = result

        # 按原始顺序排列
        primary = indexed_results[0]
        reviewers = [indexed_results[i] for i in range(1, len(all_models)) if indexed_results.get(i)]
        return primary, reviewers

    # 串行：先 primary 再 reviewers（原有逻辑）
    primary = _run_one_model(
        context,
        runtime,
        source_path,
        config,
        transcription.primary,
        "transcription.primary",
        0.12,
        0.42,
    )
    if transcription.mode != "multi_model" or not transcription.reviewers:
        return primary, []

    reviewers: list[dict[str, Any]] = []

    def handle_failure(model_config: ModelRunConfig, exc: Exception) -> None:
        if transcription.resource_failure_policy != "skip_failed_reviewer":
            raise exc
        _emit_stage(
            context,
            stage="transcription.reviewers",
            progress=0.58,
            message=f"校对模型 {model_config.model} 失败，已按策略跳过：{exc}",
            level="warning",
            model=model_config.model,
            error_code="REVIEWER_SKIPPED",
            retryable=True,
        )

    if transcription.execution == "parallel" and len(transcription.reviewers) > 1:
        workers = min(transcription.max_concurrency, len(transcription.reviewers))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pat-reviewer") as executor:
            futures = {
                executor.submit(
                    _run_one_model,
                    context,
                    runtime,
                    source_path,
                    config,
                    item,
                    "transcription.reviewers",
                    0.43,
                    0.58,
                ): item
                for item in transcription.reviewers
            }
            for future in as_completed(futures):
                item = futures[future]
                try:
                    reviewers.append(future.result())
                except Exception as exc:
                    if transcription.resource_failure_policy == "fallback_to_serial":
                        _emit_stage(
                            context,
                            stage="transcription.reviewers",
                            progress=0.58,
                            message=f"校对模型 {item.model} 并行执行失败，正在串行重试：{exc}",
                            level="warning",
                            model=item.model,
                            error_code="REVIEWER_SERIAL_RETRY",
                            retryable=True,
                        )
                        reviewers.append(
                            _run_one_model(
                                context,
                                runtime,
                                source_path,
                                config,
                                item,
                                "transcription.reviewers",
                                0.58,
                                0.6,
                            )
                        )
                    else:
                        handle_failure(item, exc)
    else:
        for index, item in enumerate(transcription.reviewers, start=1):
            context.raise_if_cancelled()
            try:
                reviewers.append(
                    _run_one_model(
                        context,
                        runtime,
                        source_path,
                        config,
                        item,
                        "transcription.reviewers",
                        0.42 + 0.16 * ((index - 1) / len(transcription.reviewers)),
                        0.42 + 0.16 * (index / len(transcription.reviewers)),
                    )
                )
            except Exception as exc:
                handle_failure(item, exc)
    reviewer_order = {
        item.model: index for index, item in enumerate(transcription.reviewers)
    }
    reviewers.sort(key=lambda item: reviewer_order.get(str(item.get("model") or ""), len(reviewer_order)))
    return primary, reviewers


def _redistribute_refined_to_segments(
    segments: list[dict],
    refined_text: str,
) -> None:
    """
    把 scope=refined/all 模式下 LLM 校对后的全文，按原始 segments[i].text 的字符长度比例，
    近似拆分回填到每个 segments[i].text。
    适用场景：LLM 仅做错别字/标点修正，前后字符数变化很小（<1%），比例切分误差 1-2 个字。
    """
    if not segments or not refined_text:
        return
    # 1) 计算每个 segment 在全文中的位置（按原始 text 长度）
    original_parts: list[str] = []
    lengths: list[int] = []
    for seg in segments:
        t = str(seg.get("text") or "")
        original_parts.append(t)
        lengths.append(len(t))
    total_orig = sum(lengths)
    if total_orig <= 0:
        return
    refined = refined_text
    total_refined = len(refined)

    # 2) 逐段按比例截取；最后一段吸纳剩余字符，防止舍入漏字。
    orig_cursor = 0
    ref_cursor = 0
    seg_count = len(segments)
    for idx, (seg, length) in enumerate(zip(segments, lengths)):
        if idx == seg_count - 1:
            piece = refined[ref_cursor:]
        else:
            orig_end = orig_cursor + length
            # 比例映射：refined 结束位置 ≈ total_refined × (orig_end/total_orig)
            # 取整，不超过字符串长度。
            ref_end = int(round(total_refined * orig_end / total_orig))
            ref_end = max(ref_cursor, min(ref_end, total_refined))
            piece = refined[ref_cursor:ref_end]
            ref_cursor = ref_end
            orig_cursor = orig_end
        seg["text"] = piece


def _run_llm_stages(
    context: WorkflowRunContext,
    runtime: WorkflowRuntime,
    config: WorkflowConfig,
    result: dict[str, Any],
) -> None:
    result.setdefault("original_text", str(result.get("text") or ""))
    for stage_name, progress, output_key in (
        ("llm_proofread", 0.78, "refined_text"),
        ("summary", 0.82, "summary"),
        ("mindmap", 0.86, "mindmap"),
    ):
        stage_config = getattr(config, stage_name)
        if not stage_config.enabled:
            continue
        if runtime.llm_stage is None:
            raise RuntimeError(f"已启用 {stage_name}，但运行时未配置 LLM 服务")
        context.raise_if_cancelled()
        _emit_stage(
            context,
            stage=stage_name,
            progress=progress - 0.02,
            message=f"{stage_name} 开始执行",
            model=stage_config.model,
        )
        if stage_name == "llm_proofread" and stage_config.scope == "segments":
            refined_parts: list[str] = []
            segments = list(result.get("segments") or [])
            for segment in segments:
                context.raise_if_cancelled()
                refined = str(
                    runtime.llm_stage(
                        stage_name,
                        str(segment.get("text") or ""),
                        stage_config,
                    )
                    or ""
                )
                segment["text"] = refined or str(segment.get("text") or "")
                refined_parts.append(str(segment["text"]))
            result["refined_text"] = "".join(refined_parts)
            result["text"] = result["refined_text"]
        else:
            if stage_config.scope == "original":
                stage_input = str(result.get("original_text") or "")
            elif stage_config.scope in {"all", "refined"}:
                stage_input = str(result.get("refined_text") or result.get("text") or "")
            else:
                stage_input = "".join(
                    str(item.get("text") or "") for item in result.get("segments") or []
                )
            stage_output = runtime.llm_stage(stage_name, stage_input, stage_config)
            result[output_key] = stage_output
            if stage_name == "llm_proofread":
                refined_text = str(stage_output or result.get("text") or "")
                result["text"] = refined_text
                # scope=refined/all 时：把校对结果按原 segments 长度比例回填到 segments[i].text，
                # 保证导出的 SRT/TXT（基于 segments）内容与 result.text 一致。
                # 校对只做错别字/标点修正，字符数变化通常 <1%，比例切分足够精确。
                if stage_config.scope != "segments":
                    segments = result.get("segments") or []
                    if segments and refined_text:
                        _redistribute_refined_to_segments(segments, refined_text)
        _emit_stage(
            context,
            stage=stage_name,
            progress=progress,
            message=f"{stage_name} 执行完成",
            level="success",
            model=stage_config.model,
        )


def run_workflow(context: WorkflowRunContext, runtime: WorkflowRuntime) -> dict[str, Any]:
    """执行一个工作流任务并返回统一结果。"""
    config = parse_workflow_config(context.config)
    source_path = context.source_path
    output_dir = str(Path(source_path).resolve().parent / "artifacts")
    context.raise_if_cancelled()
    _emit_stage(context, stage="prepare", progress=0.02, message="正在检查输入媒体")

    if config.preprocess.enabled:
        if runtime.preprocess is None:
            raise RuntimeError("已启用音频前处理，但运行时未配置前处理服务")
        if config.preprocess.silence_mode == "trim_silence":
            _emit_stage(
                context,
                stage="preprocess",
                progress=0.04,
                message="静音裁剪会改变原始时间轴，字幕时间将基于处理后音频",
                level="warning",
                error_code="TIMELINE_CHANGED_BY_TRIM",
                retryable=False,
            )
        source_path = runtime.preprocess(source_path, config.preprocess, output_dir)
        _emit_stage(context, stage="preprocess", progress=0.1, message="音频前处理完成", level="success")
    else:
        _emit_stage(context, stage="preprocess", progress=0.1, message="未启用音频前处理", level="info")

    primary, reviewers = _run_transcriptions(context, runtime, source_path, config)
    context.raise_if_cancelled()
    _emit_stage(context, stage="reconciliation", progress=0.62, message="正在比对多模型候选")
    reconciled = reconciliation_service.reconcile_transcriptions(
        primary,
        reviewers,
        mode=config.reconciliation.mode,
        disagreement_threshold=config.reconciliation.disagreement_threshold,
        keep_alternatives=config.reconciliation.keep_alternatives,
        uncertain_policy=config.reconciliation.uncertain_policy,
    )
    result: dict[str, Any] = {
        **reconciled,
        "model_runs": [primary, *reviewers],
        "workflow_version": config.workflow_version,
    }
    _emit_stage(context, stage="reconciliation", progress=0.67, message="多模型候选比对完成", level="success")

    if config.diarization.enabled:
        if runtime.diarize is None:
            raise RuntimeError("已启用说话人识别，但运行时未配置说话人服务")
        _emit_stage(
            context,
            stage="diarization",
            progress=0.68,
            message="正在生成独立说话人时间轴",
            model=config.diarization.speaker_model,
        )
        diarization = next(
            (
                item["diarization"]
                for item in (primary, *reviewers)
                if str(item.get("model") or "") == config.diarization.asr_model
                and isinstance(item.get("diarization"), dict)
            ),
            None,
        )
        if diarization is None:
            diarization = runtime.diarize(source_path, config.diarization)
        else:
            _emit_stage(
                context,
                stage="diarization",
                progress=0.7,
                message="复用已完成转录模型的说话人结果，避免重复加载与推理",
                level="info",
                model=config.diarization.asr_model,
            )
        result["diarization"] = diarization
        result["segments"] = alignment_service.align_speakers_to_segments(
            result["segments"],
            list(diarization.get("segments") or []),
        )
        _emit_stage(
            context,
            stage="diarization",
            progress=0.74,
            message="说话人时间轴对齐完成；不确定段已保留候选",
            level="success",
            model=config.diarization.speaker_model,
        )

    _run_llm_stages(context, runtime, config, result)

    if config.translation.enabled:
        if runtime.translate is None:
            raise RuntimeError("已启用翻译，但运行时未配置翻译服务")
        _emit_stage(context, stage="translation", progress=0.88, message="正在翻译", model=config.translation.model)
        result["translation"] = runtime.translate(str(result.get("text") or ""), config.translation)
        _emit_stage(context, stage="translation", progress=0.91, message="翻译完成", level="success", model=config.translation.model)

    if config.emotion.enabled:
        if runtime.emotion is None:
            raise RuntimeError("已启用情感识别，但运行时未配置情感服务")
        _emit_stage(context, stage="emotion", progress=0.92, message="正在识别情感", model=config.emotion.model)
        result["emotion"] = runtime.emotion(source_path, config.emotion)
        _emit_stage(context, stage="emotion", progress=0.94, message="情感识别完成", level="success", model=config.emotion.model)

    if config.timestamps.level == "off":
        for segment in result.get("segments") or []:
            for key in (
                "start",
                "end",
                "speaker_candidates",
                "speaker_uncertain",
                "alignment_quality",
            ):
                segment.pop(key, None)
        result.pop("words", None)
    result["timestamp_level"] = config.timestamps.level

    context.raise_if_cancelled()
    _emit_stage(context, stage="export", progress=0.96, message="正在生成导出产物")
    if runtime.write_artifacts is None:
        raise RuntimeError("工作流运行时未配置产物导出服务")
    result["artifacts"] = runtime.write_artifacts(
        output_dir=output_dir,
        result=result,
        config=context.config,
        events=context.events,
        formats=config.export.formats,
        include_raw_candidates=config.export.include_raw_candidates,
        include_config_snapshot=config.export.include_config_snapshot,
        timestamp=artifact_service._make_timestamp(),
    )
    _emit_stage(context, stage="export", progress=0.99, message="导出产物已生成", level="success")
    return result
