"""
程序说明：
Pat-FunASR 统一模型目录。

职责：
- 维护模型别名到运行配置的唯一映射。
- 维护后端能力矩阵，供 API、WebUI、批处理和预下载脚本复用。
- 返回配置副本，避免调用方修改共享常量。
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Iterable


MODEL_CONFIGS = {
    "sensevoice": {
        "model": "iic/SenseVoiceSmall",
        "hub": "ms",
        "trust_remote_code": True,
        "vad_model": "fsmn-vad",
        "vad_kwargs": {"max_single_segment_time": 30000},
    },
    "paraformer": {
        "model": "paraformer-zh",
        "hub": "ms",
        "trust_remote_code": True,
        "vad_model": "fsmn-vad",
        "punc_model": "ct-punc",
    },
    "paraformer-en": {
        "model": "paraformer-en",
        "hub": "ms",
        "trust_remote_code": True,
        "vad_model": "fsmn-vad",
    },
    "paraformer-zh-streaming": {
        "model": "paraformer-zh-streaming",
        "hub": "ms",
        "trust_remote_code": True,
        "punc_model": "ct-punc",
    },
    "fun-asr-nano": {
        "model": "FunAudioLLM/Fun-ASR-Nano-2512",
        "hub": "ms",
        "trust_remote_code": True,
        "vad_model": "fsmn-vad",
        "vad_kwargs": {"max_single_segment_time": 30000},
    },
    "qwen3-asr": {
        "model": "Qwen/Qwen3-ASR-1.7B",
        "hub": "ms",
        "trust_remote_code": True,
        "dtype": "fp16",
        "forced_aligner": "Qwen/Qwen3-ForcedAligner-0.6B",
        "vad_model": "fsmn-vad",
        "vad_kwargs": {"max_single_segment_time": 30000},
    },
    "qwen3-asr-0.6b": {
        "model": "Qwen/Qwen3-ASR-0.6B",
        "hub": "ms",
        "trust_remote_code": True,
        "dtype": "fp16",
        "forced_aligner": "Qwen/Qwen3-ForcedAligner-0.6B",
        "vad_model": "fsmn-vad",
        "vad_kwargs": {"max_single_segment_time": 30000},
    },
    "emotion2vec-plus-large": {
        "model": "iic/emotion2vec_plus_large",
        "hub": "ms",
        "trust_remote_code": True,
    },
    "nllb-200-distilled-600m": {
        "model": "facebook/nllb-200-distilled-600m",
        "hub": "ms",
        "type": "translation",
    },
    "nllb-200-distilled-1.3b": {
        "model": "facebook/nllb-200-distilled-1.3b",
        "hub": "ms",
        "type": "translation",
    },
}


MODEL_CAPABILITIES = {
    "sensevoice": {
        "kind": "asr",
        "offline_asr": True,
        "streaming_asr": False,
        "diarization": True,
        "emotion": True,
        "vad": True,
        "punc": True,
        "translation": False,
        "forced_alignment": False,
        "notes": "多语言；支持说话人分离，也可直接输出情感标签",
    },
    "paraformer": {
        "kind": "asr",
        "offline_asr": True,
        "streaming_asr": False,
        "diarization": True,
        "emotion": False,
        "vad": True,
        "punc": True,
        "translation": False,
        "forced_alignment": False,
        "notes": "中文离线识别；支持 cam++ 说话人分离",
    },
    "paraformer-en": {
        "kind": "asr",
        "offline_asr": True,
        "streaming_asr": False,
        "diarization": False,
        "emotion": False,
        "vad": True,
        "punc": False,
        "translation": False,
        "forced_alignment": False,
        "notes": "英文离线识别",
    },
    "paraformer-zh-streaming": {
        "kind": "asr",
        "offline_asr": False,
        "streaming_asr": True,
        "diarization": False,
        "emotion": False,
        "vad": False,
        "punc": True,
        "translation": False,
        "forced_alignment": False,
        "notes": "流式识别专用；默认挂载 ct-punc 提升断句与可读性",
    },
    "fun-asr-nano": {
        "kind": "asr",
        "offline_asr": True,
        "streaming_asr": False,
        "diarization": True,
        "emotion": False,
        "vad": True,
        "punc": True,
        "translation": False,
        "forced_alignment": False,
        "notes": "轻量多语言模型；支持 cam++ 说话人分离",
    },
    "qwen3-asr": {
        "kind": "asr",
        "offline_asr": True,
        "streaming_asr": False,
        "diarization": False,
        "emotion": False,
        "vad": True,
        "punc": True,
        "translation": False,
        "forced_alignment": True,
        "notes": "高精度离线识别",
    },
    "qwen3-asr-0.6b": {
        "kind": "asr",
        "offline_asr": True,
        "streaming_asr": False,
        "diarization": False,
        "emotion": False,
        "vad": True,
        "punc": True,
        "translation": False,
        "forced_alignment": True,
        "notes": "轻量版 Qwen3-ASR",
    },
    "emotion2vec-plus-large": {
        "kind": "emotion",
        "offline_asr": False,
        "streaming_asr": False,
        "diarization": False,
        "emotion": True,
        "vad": False,
        "punc": False,
        "translation": False,
        "forced_alignment": False,
        "notes": "独立情感识别模型",
    },
    "nllb-200-distilled-600m": {
        "kind": "translation",
        "offline_asr": False,
        "streaming_asr": False,
        "diarization": False,
        "emotion": False,
        "vad": False,
        "punc": False,
        "translation": True,
        "forced_alignment": False,
        "notes": "多语种文本翻译；600M 参数轻量版",
    },
    "nllb-200-distilled-1.3b": {
        "kind": "translation",
        "offline_asr": False,
        "streaming_asr": False,
        "diarization": False,
        "emotion": False,
        "vad": False,
        "punc": False,
        "translation": True,
        "forced_alignment": False,
        "notes": "多语种文本翻译；1.3B 参数高精度版",
    },
}


MODELSCOPE_MODEL_ALIASES = {
    "paraformer": "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    "paraformer-zh": "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    "paraformer-en": "iic/speech_paraformer-large-vad-punc_asr_nat-en-16k-common-vocab10020",
    "paraformer-zh-streaming": "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online",
    "fsmn-vad": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
    "ct-punc": "iic/punc_ct-transformer_cn-en-common-vocab471067-large",
    "cam++": "iic/speech_campplus_sv_zh-cn_16k-common",
}


def _has_model_payload(path: Path) -> bool:
    """判断缓存目录是否包含配置和权重类文件。"""
    if not path.is_dir():
        return False
    names = {item.name for item in path.iterdir() if item.is_file()}
    has_config = bool({"config.yaml", "configuration.json", "config.json"} & names)
    has_payload = any(
        name.endswith((".pt", ".bin", ".safetensors"))
        or name in {"tokenizer.json", "tokens.json", "tokens.txt"}
        for name in names
    )
    return has_config and has_payload


def resolve_local_model_path(
    model_id: str,
    cache_roots: Iterable[str | Path],
) -> Path | None:
    """从已存在的 ModelScope 缓存解析模型目录，不触发联网或下载。"""
    resolved_id = MODELSCOPE_MODEL_ALIASES.get(model_id, model_id)
    identifiers = [resolved_id]
    if resolved_id != model_id:
        identifiers.append(model_id)
    for root_value in cache_roots:
        root = Path(root_value).expanduser()
        for base in (root, root / "models"):
            for identifier in identifiers:
                relative = Path(*identifier.replace("___", ".").split("/"))
                candidates = [base / relative]
                if "." in identifier:
                    candidates.append(base / Path(*identifier.replace(".", "___").split("/")))
                for candidate in candidates:
                    if _has_model_payload(candidate):
                        return candidate.resolve()
    return None


def get_model_configs(aliases: Iterable[str] | None = None) -> dict[str, dict]:
    """返回全部或指定模型配置的深拷贝。"""
    selected = MODEL_CONFIGS.keys() if aliases is None else aliases
    return {alias: deepcopy(MODEL_CONFIGS[alias]) for alias in selected}


def get_model_capabilities() -> dict[str, dict]:
    """返回模型能力矩阵副本，防止调用方污染全局目录。"""
    return deepcopy(MODEL_CAPABILITIES)


def model_supports(model_name: str, capability: str) -> bool:
    """判断模型是否声明支持指定能力。"""
    return bool(MODEL_CAPABILITIES.get(model_name, {}).get(capability, False))


STREAMING_MODELS = {
    name for name in MODEL_CONFIGS if model_supports(name, "streaming_asr")
}
EMOTION_MODELS = {
    name for name in MODEL_CONFIGS if model_supports(name, "emotion")
}
DIARIZATION_MODELS = {
    name for name in MODEL_CONFIGS if model_supports(name, "diarization")
}
OFFLINE_ASR_MODELS = {
    name for name in MODEL_CONFIGS if model_supports(name, "offline_asr")
}
