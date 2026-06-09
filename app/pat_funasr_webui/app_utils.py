"""
程序说明：
Pat WebUI 辅助函数。

职责：
- 解析后端 "/v1/models" 的返回结果，生成 UI 可直接使用的模型下拉选项。
- 按白名单构建转写请求字段，避免把任意 UI 字段透传到后端。
- 统一输出格式与下载文件名的映射，便于后续下载/预览逻辑复用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_MODEL = "sensevoice"
DEFAULT_STREAMING_MODEL = "paraformer-zh-streaming"
DEFAULT_EMOTION_MODEL = "emotion2vec-plus-large"
DEFAULT_DIARIZATION_MODEL = "paraformer"
TEXT_RESPONSE_FORMATS = {"json", "verbose_json"}
BINARY_RESPONSE_FORMATS = {"txt", "srt", "vtt", "tsv", "all"}
STREAMING_MODEL_IDS = {"paraformer-zh-streaming"}
EMOTION_MODEL_IDS = {"emotion2vec-plus-large", "sensevoice"}
DIARIZATION_MODEL_IDS = {"paraformer", "fun-asr-nano", "sensevoice"}
VIDEO_FILE_SUFFIXES = (
    ".mp4",
    ".avi",
    ".mov",
    ".mkv",
    ".flv",
    ".wmv",
    ".webm",
    ".m4v",
    ".mpg",
    ".mpeg",
    ".3gp",
    ".ts",
    ".mts",
    ".m2ts",
    ".vob",
    ".ogv",
    ".rm",
    ".rmvb",
)
AUDIO_FILE_SUFFIXES = (
    ".wav",
    ".mp3",
    ".m4a",
    ".flac",
    ".ogg",
    ".aac",
    ".wma",
    ".opus",
)
MEDIA_FILE_SUFFIXES = tuple(sorted(set(VIDEO_FILE_SUFFIXES + AUDIO_FILE_SUFFIXES)))
ALLOWED_REQUEST_FIELDS = (
    "model",
    "response_format",
    "language",
    "vad_preset",
    "vad_max_single_segment_time",
    "merge_vad",
    "merge_length_s",
    "max_line_width",
    "hotword",
    "use_itn",
    "batch_size_s",
    "batch_size_threshold_s",
    "punc_mode",
    "device",
    "hub",
    "disable_update",
    "ncpu",
    "log_level",
    "disable_pbar",
)

MODEL_LABELS = {
    "sensevoice": "SenseVoice",
    "paraformer": "Paraformer 中文",
    "paraformer-en": "Paraformer 英文",
    "paraformer-zh-streaming": "Paraformer Streaming 中文",
    "fun-asr-nano": "Fun-ASR-Nano",
    "qwen3-asr": "Qwen3-ASR-1.7B",
    "qwen3-asr-0.6b": "Qwen3-ASR-0.6B",
    "emotion2vec-plus-large": "Emotion2Vec Plus Large",
}

MODEL_LANGUAGE_MATRIX = {
    "sensevoice": "明确列出：普通话、粤语、英语、日语、韩语；另称总体支持 50+ 语种，但未公开完整名单",
    "paraformer": "官方 Model Zoo：中文和英文；未见官方单独列出方言清单",
    "paraformer-en": "官方 Model Zoo：英文；未见官方单独列出方言清单",
    "paraformer-zh-streaming": "官方 Model Zoo：中文和英文；未见官方单独列出方言清单",
    "fun-asr-nano": "中文、英文、日文；中文含 7 种方言（吴语、粤语、闽语、客家话、赣语、湘语、晋语），官方另称 26 种地域口音",
    "qwen3-asr": "30 种语言：中文、英语、粤语、阿拉伯语、德语、法语、西班牙语、葡萄牙语、印尼语、意大利语、韩语、俄语、泰语、越南语、日语、土耳其语、印地语、马来语、荷兰语、瑞典语、丹麦语、芬兰语、波兰语、捷克语、菲律宾语、波斯语、希腊语、匈牙利语、马其顿语、罗马尼亚语；22 种中文方言/口音：安徽、东北、福建、甘肃、贵州、河北、河南、湖北、湖南、江西、宁夏、山东、陕西、山西、四川、天津、云南、浙江、粤语（香港口音）、粤语（广东口音）、吴语、闽南语",
    "qwen3-asr-0.6b": "30 种语言：中文、英语、粤语、阿拉伯语、德语、法语、西班牙语、葡萄牙语、印尼语、意大利语、韩语、俄语、泰语、越南语、日语、土耳其语、印地语、马来语、荷兰语、瑞典语、丹麦语、芬兰语、波兰语、捷克语、菲律宾语、波斯语、希腊语、匈牙利语、马其顿语、罗马尼亚语；22 种中文方言/口音：安徽、东北、福建、甘肃、贵州、河北、河南、湖北、湖南、江西、宁夏、山东、陕西、山西、四川、天津、云南、浙江、粤语（香港口音）、粤语（广东口音）、吴语、闽南语",
    "emotion2vec-plus-large": "README 未枚举具体语种；定位为通用语音情感识别模型",
}

MODEL_ENTRY_MATRIX = {
    "sensevoice": "离线识别 / 说话人分离 / 情感识别",
    "paraformer": "离线识别 / 说话人分离",
    "paraformer-en": "离线识别",
    "paraformer-zh-streaming": "流式识别",
    "fun-asr-nano": "离线识别 / 说话人分离",
    "qwen3-asr": "离线识别",
    "qwen3-asr-0.6b": "离线识别",
    "emotion2vec-plus-large": "情感识别",
}

MODEL_CAPABILITY_MATRIX = {
    "sensevoice": {
        "offline_asr": True,
        "streaming_asr": False,
        "diarization": True,
        "emotion": True,
        "vad": True,
        "punc": True,
        "notes": "官方 README 明确列出普通话/粤语/英语/日语/韩语，并给出 auto/zh/en/yue/ja/ko/nospeech 语言码；同时宣称总体支持 50+ 语种，但未公开完整名单；支持说话人分离与情感标签",
    },
    "paraformer": {
        "offline_asr": True,
        "streaming_asr": False,
        "diarization": True,
        "emotion": False,
        "vad": True,
        "punc": True,
        "notes": "本地 alias 对应 paraformer-zh，官方 Model Zoo 标注语言为中文和英文；未见官方单独列出中文方言清单；支持 cam++ 说话人分离",
    },
    "paraformer-en": {
        "offline_asr": True,
        "streaming_asr": False,
        "diarization": False,
        "emotion": False,
        "vad": True,
        "punc": False,
        "notes": "官方 Model Zoo 标注语言为英文；未见官方单独列出英文方言/口音清单",
    },
    "paraformer-zh-streaming": {
        "offline_asr": False,
        "streaming_asr": True,
        "diarization": False,
        "emotion": False,
        "vad": False,
        "punc": True,
        "notes": "本地 alias 对应 Paraformer 在线版，官方 Model Zoo 标注语言为中文和英文；未见官方单独列出方言清单；默认挂载 ct-punc 提升断句与可读性",
    },
    "fun-asr-nano": {
        "offline_asr": True,
        "streaming_asr": False,
        "diarization": True,
        "emotion": False,
        "vad": True,
        "punc": True,
        "notes": "官方 README_zh 当前模型页表格写中文/英文/日文；中文明确包含 7 种方言（吴语、粤语、闽语、客家话、赣语、湘语、晋语），并宣称支持 26 种地域口音；README 当前公开列举的地域口音样例包括河南、陕西、湖北、四川、重庆、云南、贵州、广东、广西、河北、天津、山东、安徽、南京、江苏、杭州、甘肃、宁夏；支持 cam++ 说话人分离",
    },
    "qwen3-asr": {
        "offline_asr": True,
        "streaming_asr": False,
        "diarization": False,
        "emotion": False,
        "vad": True,
        "punc": True,
        "notes": "官方 README 明确列出 30 种语言与 22 种中文方言/口音；当前项目只接了离线路径，未接其原生 streaming/vLLM 工具链",
    },
    "qwen3-asr-0.6b": {
        "offline_asr": True,
        "streaming_asr": False,
        "diarization": False,
        "emotion": False,
        "vad": True,
        "punc": True,
        "notes": "官方 README 明确列出 30 种语言与 22 种中文方言/口音；当前项目只接了离线路径，未接其原生 streaming/vLLM 工具链",
    },
    "emotion2vec-plus-large": {
        "offline_asr": False,
        "streaming_asr": False,
        "diarization": False,
        "emotion": True,
        "vad": False,
        "punc": False,
        "notes": "独立情感识别模型；README 强调跨语种与跨场景鲁棒性，但未给出精确语种枚举",
    },
}

CAPABILITY_FILTER_LABELS = {
    "all": "全部模型",
    "offline_asr": "离线识别",
    "streaming_asr": "流式识别",
    "diarization": "说话人分离",
    "emotion": "情感识别",
    "vad": "VAD",
    "punc": "PUNC",
}
CAPABILITY_FILTER_CHOICES = [
    (label, key) for key, label in CAPABILITY_FILTER_LABELS.items()
]
CAPABILITY_TARGETS = {
    "all": {
        "tab": "服务与调试",
        "area": "模型能力看板",
        "notes": "先看当前后端已暴露的能力，再决定进入哪个功能页。",
    },
    "offline_asr": {
        "tab": "离线识别",
        "area": "模型 + 高级参数",
        "notes": "适合常规音视频识别、多格式导出与批量任务。",
    },
    "streaming_asr": {
        "tab": "流式识别",
        "area": "流式模型 + 分块大小(chunk_size)",
        "notes": "适合低延迟实时识别，只能选支持流式的模型。",
    },
    "diarization": {
        "tab": "说话人分离",
        "area": "spk_model + spk_mode + 导出下载",
        "notes": "适合输出带 speaker 分段结果，并直接下载 txt/srt/vtt/tsv/zip。",
    },
    "emotion": {
        "tab": "情感识别",
        "area": "模型 + granularity",
        "notes": "适合输出整体情感排序，后续再补时间片能力。",
    },
    "vad": {
        "tab": "离线识别",
        "area": "高级参数 / 增强能力预留",
        "notes": "VAD 属于离线识别增强项，不单独拆流程页。",
    },
    "punc": {
        "tab": "离线识别",
        "area": "增强能力预留",
        "notes": "PUNC 属于离线识别增强项，不单独拆流程页。",
    },
}

FORMAT_FILENAMES = {
    "json": "output.json",
    "verbose_json": "output.verbose.json",
    "txt": "output.txt",
    "srt": "output.srt",
    "vtt": "output.vtt",
    "tsv": "output.tsv",
    "all": "output.zip",
}


def normalize_bool(value: Any) -> str | None:
    """把布尔值统一编码为后端表单能识别的字符串。"""
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "auto"}:
            return None
        if normalized in {"true", "1", "yes", "on"}:
            return "true"
        if normalized in {"false", "0", "no", "off"}:
            return "false"
    return "true" if bool(value) else "false"


def build_request_fields(**kwargs: Any) -> dict[str, str]:
    """按白名单构建请求字段，过滤 None/空字符串。"""
    fields: dict[str, str] = {}
    for field_name in ALLOWED_REQUEST_FIELDS:
        value = kwargs.get(field_name)
        if value is None or value == "":
            continue
        if field_name in {"batch_size_s", "batch_size_threshold_s", "vad_max_single_segment_time", "ncpu"}:
            try:
                if int(value) <= 0:
                    continue
            except Exception:
                continue
        if field_name in {"merge_vad", "use_itn", "disable_update", "disable_pbar"}:
            encoded = normalize_bool(value)
            if encoded is not None:
                fields[field_name] = encoded
            continue
        fields[field_name] = str(value)
    return fields


def format_model_label(model_id: str, ready: bool) -> str:
    """为模型下拉框生成更易读的展示文本。"""
    base_label = MODEL_LABELS.get(model_id, model_id)
    ready_label = "已加载" if ready else "按需加载"
    return f"{base_label} ({model_id}) [{ready_label}]"


def parse_model_choices(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """解析 "/v1/models" 响应，生成 Gradio Dropdown 选项。"""
    choices: list[tuple[str, str]] = []
    for item in payload.get("data", []):
        model_id = str(item.get("id", "")).strip()
        if not model_id:
            continue
        ready = bool(item.get("ready", False))
        choices.append((format_model_label(model_id, ready), model_id))
    return choices


def build_known_model_choices() -> list[tuple[str, str]]:
    """构建静态已知模型下拉选项，供接口异常时兜底。"""
    return [(format_model_label(model_id, ready=False), model_id) for model_id in MODEL_LABELS]


def choose_default_model(choices: list[tuple[str, str]], fallback: str = DEFAULT_MODEL) -> str | None:
    """优先选择默认模型；若不存在则选第一个。"""
    values = [value for _, value in choices]
    if fallback in values:
        return fallback
    if values:
        return values[0]
    return None


def ensure_dropdown_choices(
    choices: list[tuple[str, str]],
    *,
    fallback: str,
) -> list[tuple[str, str]]:
    """确保下拉框至少包含一个可选项，避免 value 不在 choices 中。"""
    if choices:
        return choices
    return [(format_model_label(fallback, ready=False), fallback)]


def filter_streaming_model_choices(choices: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """过滤出支持 Streaming 的模型选项。"""
    return [(label, value) for label, value in choices if value in STREAMING_MODEL_IDS]


def choose_default_streaming_model(choices: list[tuple[str, str]]) -> str | None:
    """优先选择默认流式模型；若不存在则选第一个。"""
    return choose_default_model(choices, fallback=DEFAULT_STREAMING_MODEL)


def filter_emotion_model_choices(choices: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """过滤出支持情感识别的模型选项。"""
    return [(label, value) for label, value in choices if value in EMOTION_MODEL_IDS]


def choose_default_emotion_model(choices: list[tuple[str, str]]) -> str | None:
    """优先选择默认情感模型；若不存在则选第一个。"""
    return choose_default_model(choices, fallback=DEFAULT_EMOTION_MODEL)


def filter_diarization_model_choices(choices: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """过滤出支持说话人分离的模型选项。"""
    return [(label, value) for label, value in choices if value in DIARIZATION_MODEL_IDS]


def choose_default_diarization_model(choices: list[tuple[str, str]]) -> str | None:
    """优先选择默认说话人分离模型；若不存在则选第一个。"""
    return choose_default_model(choices, fallback=DEFAULT_DIARIZATION_MODEL)


def is_video_file(path: str | Path | None) -> bool:
    """判断文件路径是否为视频文件。"""
    if not path:
        return False
    return Path(path).suffix.lower() in VIDEO_FILE_SUFFIXES


def _capability_badge(enabled: bool) -> str:
    return "Y" if enabled else "-"


def _status_label(ready: bool) -> str:
    return "已加载" if ready else "按需加载"


def _capability_text(capability: dict[str, Any]) -> str:
    labels = []
    if capability.get("offline_asr"):
        labels.append("离线识别")
    if capability.get("streaming_asr"):
        labels.append("流式识别")
    if capability.get("diarization"):
        labels.append("说话人分离")
    if capability.get("emotion"):
        labels.append("情感识别")
    if capability.get("vad"):
        labels.append("VAD")
    if capability.get("punc"):
        labels.append("PUNC")
    return " / ".join(labels) if labels else "-"


def summarize_model_status(payload: dict[str, Any]) -> str:
    """生成简短的模型状态摘要。"""
    rows = build_model_capability_rows(payload)
    if not rows:
        return "未获取到模型列表"
    loaded = [f"`{row['model']}`" for row in rows if row.get("ready") == "已加载"]
    pending = [f"`{row['model']}`" for row in rows if row.get("ready") != "已加载"]
    loaded_text = "、".join(loaded) if loaded else "无"
    pending_text = "、".join(pending) if pending else "无"
    return (
        f"共 {len(rows)} 个模型；"
        f"已加载 {len(loaded)} 个：{loaded_text}；"
        f"按需加载 {len(pending)} 个：{pending_text}"
    )


def build_model_capability_rows(payload: dict[str, Any]) -> list[dict[str, str]]:
    """把模型列表和静态能力矩阵合并为展示行。"""
    rows: list[dict[str, str]] = []
    for item in payload.get("data", []):
        model_id = str(item.get("id", "")).strip()
        if not model_id:
            continue
        ready = bool(item.get("ready", False))
        capability = item.get("capabilities") or MODEL_CAPABILITY_MATRIX.get(model_id, {})
        languages = MODEL_LANGUAGE_MATRIX.get(model_id, "以模型文档为准")
        recommended_entry = MODEL_ENTRY_MATRIX.get(model_id, "服务与调试")
        rows.append(
            {
                "model": model_id,
                "label": MODEL_LABELS.get(model_id, model_id),
                "ready": _status_label(ready),
                "offline_asr": _capability_badge(bool(capability.get("offline_asr", False))),
                "streaming_asr": _capability_badge(bool(capability.get("streaming_asr", False))),
                "diarization": _capability_badge(bool(capability.get("diarization", False))),
                "emotion": _capability_badge(bool(capability.get("emotion", False))),
                "vad": _capability_badge(bool(capability.get("vad", False))),
                "punc": _capability_badge(bool(capability.get("punc", False))),
                "languages": languages,
                "capabilities_text": _capability_text(capability),
                "entry": recommended_entry,
                "notes": str(capability.get("notes", "")),
            }
        )
    return rows


def filter_model_capability_rows(
    rows: list[dict[str, str]], capability_filter: str = "all"
) -> list[dict[str, str]]:
    """按能力过滤模型看板行。"""
    if capability_filter == "all":
        return rows
    if capability_filter not in CAPABILITY_FILTER_LABELS:
        return rows
    return [row for row in rows if row.get(capability_filter) == "Y"]


def render_model_capability_markdown(payload: dict[str, Any], capability_filter: str = "all") -> str:
    """渲染模型能力看板 Markdown。"""
    rows = build_model_capability_rows(payload)
    rows = filter_model_capability_rows(rows, capability_filter)
    if not rows:
        filter_label = CAPABILITY_FILTER_LABELS.get(capability_filter, "当前筛选")
        return f"### 模型能力看板\n\n当前筛选“{filter_label}”下暂无匹配模型。"

    filter_label = CAPABILITY_FILTER_LABELS.get(capability_filter, "全部模型")
    lines = [
        "### 模型能力看板",
        "",
        f"- 当前筛选：`{filter_label}`",
        "- 状态说明：`已加载` 表示当前进程已缓存该模型；`按需加载` 表示服务启动时不预加载，首次请求时自动加载。",
        "",
        "| 模型 | 当前状态 | 支持语言 | 能力 | 推荐入口 | 说明 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['label']} (`{row['model']}`) | {row['ready']} | {row['languages']} | {row['capabilities_text']} | {row['entry']} | {row['notes']} |"
        )
    return "\n".join(lines)


def render_service_overview_markdown(
    payload: dict[str, Any],
    *,
    base_url: str,
    capability_filter: str = "all",
) -> str:
    """渲染服务与调试页顶部概览。"""
    rows = filter_model_capability_rows(build_model_capability_rows(payload), capability_filter)
    filter_label = CAPABILITY_FILTER_LABELS.get(capability_filter, "全部模型")
    loaded = [f"`{row['model']}`" for row in rows if row.get("ready") == "已加载"]
    pending = [f"`{row['model']}`" for row in rows if row.get("ready") != "已加载"]
    loaded_text = "、".join(loaded) if loaded else "无"
    pending_text = "、".join(pending) if pending else "无"
    return "\n".join(
        [
            "### 运行概览",
            "",
            f"- API 地址：`{base_url.rstrip('/')}`",
            "- 启动方式：后端启动时不预加载模型；首次调用对应能力时再按需加载。",
            "- 状态说明：`已加载` = 当前进程已缓存；`按需加载` = 当前未载入，但请求时会自动加载。",
            "- 模型语言：优先展示当前项目已整理的官方口径；细节以上游模型文档为准。",
            f"- 当前筛选：`{filter_label}`",
            f"- 已加载模型：{loaded_text}",
            f"- 按需加载模型：{pending_text}",
        ]
    )


def render_capability_target_markdown(payload: dict[str, Any], capability_filter: str = "all") -> str:
    """根据当前能力筛选，给出建议入口与推荐模型。"""
    target = CAPABILITY_TARGETS.get(capability_filter, CAPABILITY_TARGETS["all"])
    rows = filter_model_capability_rows(build_model_capability_rows(payload), capability_filter)
    recommended_models = "、".join(f"`{row['model']}`" for row in rows[:5]) if rows else "暂无匹配模型"
    filter_label = CAPABILITY_FILTER_LABELS.get(capability_filter, "全部模型")
    return "\n".join(
        [
            "### 使用建议",
            "",
            f"- 当前能力：`{filter_label}`",
            f"- 建议页面：`{target['tab']}`",
            f"- 重点区域：`{target['area']}`",
            f"- 推荐模型：{recommended_models}",
            f"- 说明：{target['notes']}",
        ]
    )


def is_binary_response_format(response_format: str) -> bool:
    """判断当前格式是否应该按文件/二进制结果处理。"""
    return response_format in BINARY_RESPONSE_FORMATS


def output_filename_for_format(response_format: str) -> str:
    """根据输出格式生成默认下载文件名。"""
    return FORMAT_FILENAMES.get(response_format, "output.txt")


def normalize_uploaded_paths(files: Any) -> list[str]:
    """把 Gradio 不同形态的上传结果统一转换为路径列表。"""
    if files is None:
        return []
    if isinstance(files, (str, Path)):
        return [str(files)]

    normalized: list[str] = []
    for item in files:
        if isinstance(item, (str, Path)):
            normalized.append(str(item))
            continue
        if isinstance(item, dict):
            candidate = item.get("path") or item.get("name")
            if candidate:
                normalized.append(str(candidate))
            continue
        candidate = getattr(item, "path", None) or getattr(item, "name", None)
        if candidate:
            normalized.append(str(candidate))
    return normalized


def summarize_batch_results(
    results: list[dict[str, Any]],
    *,
    max_items: int = 80,
    max_error_message_chars: int = 180,
) -> str:
    """生成批量任务汇总文本。"""
    total = len(results)
    success_count = sum(
        1 for item in results if item.get("status") == "success" or item.get("ok") is True
    )
    failed_count = sum(
        1 for item in results if item.get("status") == "error" or item.get("ok") is False
    )
    running_count = sum(1 for item in results if item.get("status") == "running")
    pending_count = sum(1 for item in results if item.get("status") == "pending")
    done_count = success_count + failed_count

    lines = [
        f"总计：{total}",
        f"进度：{done_count}/{total}",
        f"成功：{success_count}",
        f"失败：{failed_count}",
        f"进行中：{running_count}",
        f"待处理：{pending_count}",
        "",
    ]
    max_items = max(0, int(max_items))
    displayed = results if max_items <= 0 or len(results) <= max_items else results[-max_items:]
    truncated_count = len(results) - len(displayed)
    if truncated_count > 0:
        lines.append(f"提示：列表过长，已仅显示最后 {len(displayed)} 项，省略 {truncated_count} 项。")
        lines.append("")

    for item in displayed:
        file_name = item.get("file_name", "unknown")
        status = item.get("status")
        if status == "running":
            lines.append(f"[RUN] {file_name}")
        elif status == "pending":
            lines.append(f"[TODO] {file_name}")
        elif status == "success" or item.get("ok"):
            lines.append(f"[OK] {file_name}")
        else:
            message = str(item.get("message", "未知错误") or "未知错误")
            if max_error_message_chars > 0 and len(message) > int(max_error_message_chars):
                message = message[: int(max_error_message_chars)] + "..."
            lines.append(f"[ERR] {file_name} -> {message}")
    return "\n".join(lines).strip()


def initialize_batch_results(paths: list[str]) -> list[dict[str, Any]]:
    """初始化批量任务状态，默认全部为待处理。"""
    return [
        {
            "file_name": Path(path).name,
            "source_path": path,
            "status": "pending",
            "message": "",
            "result_path": "",
        }
        for path in paths
    ]
