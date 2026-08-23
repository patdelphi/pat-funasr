"""
程序说明：
精细转录工作流前端适配器，负责配置组装和实时事件文本渲染。
"""

from __future__ import annotations

from typing import Any


def _llm_stage(enabled: bool, selection: str | None) -> dict[str, Any]:
    value = str(selection or "")
    profile_id, model = (value.split("|", 1) + [""])[:2] if "|" in value else (value, "")
    return {
        "enabled": bool(enabled),
        "provider_profile_id": profile_id,
        "model": model,
        "scope": "all",
        "template_id": "default",
        "preserve_timestamps": True,
        "preserve_speakers": True,
    }


def build_workflow_config(values: dict[str, Any]) -> dict[str, Any]:
    """把前端字段映射为后端严格 workflow schema，所有选择均显式保留。"""
    reviewer_models = list(values.get("reviewer_models") or [])
    transcription_mode = str(values.get("transcription_mode") or "single_model")
    if transcription_mode == "single_model":
        reviewer_models = []
    primary_model = str(values.get("primary_model") or "sensevoice")
    primary_weight = float(values.get("primary_weight") or 1.0)
    reviewer_weight = float(values.get("reviewer_weight") or 1.0)
    return {
        "workflow_version": "1.0",
        "preset_id": str(values.get("preset_id") or "custom"),
        "preprocess": {
            "enabled": bool(values.get("preprocess_enabled", False)),
            "noise_reduction": bool(values.get("noise_reduction", False)),
            "noise_strength": float(values.get("noise_strength") or 8.0),
            "sample_rate": int(values.get("sample_rate") or 16000),
            "loudnorm": bool(values.get("loudnorm", True)),
            "silence_mode": str(values.get("silence_mode") or "preserve_timeline"),
        },
        "segmentation": {
            "vad_enabled": bool(values.get("vad_enabled", True)),
            "vad_preset": str(values.get("vad_preset") or "default"),
            "chunk_enabled": bool(values.get("chunk_enabled", False)),
            "chunk_seconds": int(values.get("chunk_seconds") or 240),
            "overlap_seconds": int(values.get("overlap_seconds") or 10),
        },
        "transcription": {
            "mode": transcription_mode,
            "primary": {
                "model": primary_model,
                "weight": primary_weight,
                "language": str(values.get("language") or "auto"),
                "hotword": str(values.get("hotword") or ""),
                "use_itn": values.get("use_itn"),
                "punc_mode": str(values.get("punc_mode") or "auto"),
            },
            "reviewers": [
                {
                    "model": model,
                    "weight": reviewer_weight,
                    "language": str(values.get("language") or "auto"),
                    "hotword": str(values.get("hotword") or ""),
                    "use_itn": values.get("use_itn"),
                    "punc_mode": str(values.get("punc_mode") or "auto"),
                }
                for model in reviewer_models
                if model and model != primary_model
            ],
            "execution": str(values.get("execution") or "serial"),
            "max_concurrency": int(values.get("max_concurrency") or 1),
            "resource_failure_policy": str(values.get("resource_failure_policy") or "stop_and_ask"),
        },
        "timestamps": {
            "level": str(values.get("timestamp_level") or "segment"),
            "forced_alignment": bool(values.get("forced_alignment", False)),
            "aligner_model": str(values.get("aligner_model") or ""),
        },
        "diarization": {
            "enabled": bool(values.get("diarization_enabled", False)),
            "strategy": str(values.get("diarization_strategy") or "separate_align"),
            "asr_model": str(values.get("diarization_asr_model") or "paraformer"),
            "speaker_model": str(values.get("speaker_model") or "cam++"),
            "spk_mode": str(values.get("spk_mode") or "punc_segment"),
            "preset_speaker_count": values.get("preset_speaker_count"),
            "global_speaker_clustering": bool(values.get("global_speaker_clustering", True)),
        },
        "reconciliation": {
            "mode": str(values.get("reconciliation_mode") or "primary_first"),
            "disagreement_threshold": float(values.get("disagreement_threshold") or 0.2),
            "keep_alternatives": bool(values.get("keep_alternatives", True)),
            "uncertain_policy": str(values.get("uncertain_policy") or "flag_for_review"),
        },
        "llm_proofread": _llm_stage(
            bool(values.get("llm_proofread_enabled", False)),
            values.get("llm_proofread_selection"),
        ),
        "summary": _llm_stage(
            bool(values.get("summary_enabled", False)),
            values.get("summary_selection"),
        ),
        "mindmap": _llm_stage(
            bool(values.get("mindmap_enabled", False)),
            values.get("mindmap_selection"),
        ),
        "translation": {
            "enabled": bool(values.get("translation_enabled", False)),
            "model": str(values.get("translation_model") or "nllb-200-distilled-600m"),
            "source_lang": str(values.get("source_lang") or ""),
            "target_lang": str(values.get("target_lang") or ""),
        },
        "emotion": {
            "enabled": bool(values.get("emotion_enabled", False)),
            "model": str(values.get("emotion_model") or "emotion2vec-plus-large"),
            "granularity": str(values.get("emotion_granularity") or "utterance"),
        },
        "export": {
            "formats": list(values.get("export_formats") or ["json", "txt"]),
            "include_raw_candidates": bool(values.get("include_raw_candidates", False)),
            "include_config_snapshot": bool(values.get("include_config_snapshot", True)),
        },
    }


def render_workflow_events(events: list[dict[str, Any]]) -> str:
    """将追加式状态事件渲染为可复制日志，保留 warning/error 和错误码。"""
    lines: list[str] = []
    for event in events:
        progress = event.get("progress")
        progress_text = f"[{int(float(progress) * 100):02d}%]" if progress is not None else "[--%]"
        level = str(event.get("level") or "info").upper()
        stage = str(event.get("stage") or "workflow")
        model = f" [{event['model']}]" if event.get("model") else ""
        code = f" ({event['error_code']})" if event.get("error_code") else ""
        lines.append(
            f"#{event.get('event_id', '-')} {progress_text} {level:<8} {stage}{model}: "
            f"{event.get('message', '')}{code}"
        )
    return "\n".join(lines)
