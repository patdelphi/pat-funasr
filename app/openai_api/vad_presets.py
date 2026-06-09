"""
程序说明：
VAD 预设参数映射（纯函数、可单测）。

目标：
- 将 API 入参 vad_preset 映射为 FunASR VAD 的一组安全参数
- 避免对外暴露任意 vad_kwargs，降低不可控风险
"""

from __future__ import annotations

from typing import Any, Dict, Optional


_ALLOWED_PRESETS = {"default", "anti_hallucination"}


def allowed_presets() -> set[str]:
    return set(_ALLOWED_PRESETS)


def build_vad_kwargs_for_preset(preset: str) -> Dict[str, Any]:
    if preset not in _ALLOWED_PRESETS:
        raise ValueError(f"unsupported vad_preset: {preset}")

    if preset == "default":
        return {}

    # anti_hallucination：更激进地过滤静音/噪声段
    # 说明：这些参数来自 VADXOptions（fsmn-vad），单位均为毫秒（ms）。
    return {
        "max_start_silence_time": 1000,
        "max_end_silence_time": 500,
        "sil_to_speech_time_thres": 100,
        "speech_to_sil_time_thres": 100,
        "speech_noise_thres": 0.7,
    }


def apply_vad_controls(
    *,
    generate_kwargs: Dict[str, Any],
    vad_preset: Optional[str],
    merge_vad: Optional[bool],
    merge_length_s: Optional[int],
    vad_max_single_segment_time: Optional[int],
) -> Dict[str, Any]:
    out = dict(generate_kwargs)

    if vad_preset:
        out.update(build_vad_kwargs_for_preset(vad_preset))

    if merge_vad is not None:
        out["merge_vad"] = bool(merge_vad)

    if merge_length_s is not None:
        if merge_length_s <= 0:
            raise ValueError("merge_length_s must be > 0")
        out["merge_length_s"] = int(merge_length_s)

    if vad_max_single_segment_time is not None:
        if vad_max_single_segment_time <= 0:
            raise ValueError("vad_max_single_segment_time must be > 0")
        vad_kwargs = dict(out.get("vad_kwargs") or {})
        vad_kwargs["max_single_segment_time"] = int(vad_max_single_segment_time)
        out["vad_kwargs"] = vad_kwargs

    return out
