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
        # 挂载 fsmn-vad：让 FunASR inference_with_vad 先切 ~30s 语音段 + batch_size_s 打包
        # → 每段语音足够短，max_new_tokens=512 不会截断
        # 外层 ffmpeg chunking 默认关闭（只对 >90min 极端长兜底）
        "vad_model": "fsmn-vad",
        "vad_kwargs": {"max_single_segment_time": 30000},
        # forced_aligner 不默认填——仅当用户显式启用字级时间戳时才通过
        # aligner_model 参数注入，避免不必要的模型下载依赖
    },
    "qwen3-asr-0.6b": {
        "model": "Qwen/Qwen3-ASR-0.6B",
        "hub": "ms",
        "trust_remote_code": True,
        "dtype": "fp16",
        # 同上：挂载 vad_model 让 FunASR VAD 切短段，避免 qwen-asr 原生分块 1200s 被 max_new_tokens 截断
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
    # paraformer 与 paraformer-zh 同指 seaco 版，与 MODEL_CONFIGS["paraformer"]["model"] 保持一致，避免解析出旧版模型
    "paraformer": "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
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


def _iter_hf_snapshot_dirs(cache_root: Path, model_id: str) -> Iterable[Path]:
    """从 HuggingFace Hub 标准缓存布局中枚举快照目录。

    HF Hub 布局: <cache>/models--<org>--<name>/snapshots/<hash>/config.json
    """
    # HF model id: "Org/Name"  →  "models--Org--Name"
    hf_folder_name = "models--" + model_id.replace("/", "--")
    model_dir = cache_root / hf_folder_name
    if not model_dir.is_dir():
        return
    snapshots_dir = model_dir / "snapshots"
    if not snapshots_dir.is_dir():
        return
    # 最新的快照（按 mtime 倒序）
    snapshots = sorted(snapshots_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for snapshot in snapshots:
        if snapshot.is_dir():
            yield snapshot


def resolve_local_model_path(
    model_id: str,
    cache_roots: Iterable[str | Path],
) -> Path | None:
    """从本地缓存解析模型目录，不触发联网或下载。

    同时兼容 ModelScope 布局（``Org/Name/``、``Org___Name/``）
    与 HuggingFace Hub 布局（``models--Org--Name/snapshots/<hash>/``）。
    """
    resolved_id = MODELSCOPE_MODEL_ALIASES.get(model_id, model_id)
    identifiers = [resolved_id]
    if resolved_id != model_id:
        identifiers.append(model_id)
    for root_value in cache_roots:
        root = Path(root_value).expanduser()
        if not root.is_dir():
            continue

        # 1) ModelScope 布局：root/Org/Name/  或  root/Org___Name/
        for base in (root, root / "models"):
            for identifier in identifiers:
                relative = Path(*identifier.replace("___", ".").split("/"))
                candidates = [base / relative]
                if "." in identifier:
                    candidates.append(base / Path(*identifier.replace(".", "___").split("/")))
                for candidate in candidates:
                    if _has_model_payload(candidate):
                        return candidate.resolve()

        # 2) HuggingFace Hub 布局：root/models--Org--Name/snapshots/<hash>/
        for identifier in identifiers:
            for snapshot in _iter_hf_snapshot_dirs(root, identifier):
                if _has_model_payload(snapshot):
                    return snapshot.resolve()
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
