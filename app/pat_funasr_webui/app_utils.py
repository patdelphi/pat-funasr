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
TEXT_RESPONSE_FORMATS = {"json", "verbose_json"}
BINARY_RESPONSE_FORMATS = {"txt", "srt", "vtt", "tsv", "all"}
ALLOWED_REQUEST_FIELDS = (
    "model",
    "response_format",
    "language",
    "vad_preset",
    "merge_vad",
    "merge_length_s",
    "max_line_width",
    "hotword",
    "use_itn",
)

MODEL_LABELS = {
    "sensevoice": "SenseVoice 多语言",
    "paraformer": "Paraformer 中文",
    "paraformer-en": "Paraformer 英文",
    "fun-asr-nano": "Fun-ASR-Nano",
    "qwen3-asr": "Qwen3-ASR-1.7B",
    "qwen3-asr-0.6b": "Qwen3-ASR-0.6B",
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
        if field_name in {"merge_vad", "use_itn"}:
            encoded = normalize_bool(value)
            if encoded is not None:
                fields[field_name] = encoded
            continue
        fields[field_name] = str(value)
    return fields


def format_model_label(model_id: str, ready: bool) -> str:
    """为模型下拉框生成更易读的展示文本。"""
    base_label = MODEL_LABELS.get(model_id, model_id)
    ready_label = "ready" if ready else "lazy-load"
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


def choose_default_model(choices: list[tuple[str, str]], fallback: str = DEFAULT_MODEL) -> str | None:
    """优先选择默认模型；若不存在则选第一个。"""
    values = [value for _, value in choices]
    if fallback in values:
        return fallback
    if values:
        return values[0]
    return None


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


def summarize_batch_results(results: list[dict[str, Any]]) -> str:
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
    for item in results:
        file_name = item.get("file_name", "unknown")
        status = item.get("status")
        if status == "running":
            lines.append(f"[RUN] {file_name}")
        elif status == "pending":
            lines.append(f"[TODO] {file_name}")
        elif status == "success" or item.get("ok"):
            lines.append(f"[OK] {file_name}")
        else:
            lines.append(f"[ERR] {file_name} -> {item.get('message', '未知错误')}")
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
