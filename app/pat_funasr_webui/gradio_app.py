# -*- coding: utf-8 -*-
"""
程序说明：
Pat WebUI 前端入口。

当前目标：
- 保持与现有 OpenAI-Compatible API 兼容。
- 优先提供独立入口、动态模型列表与可扩展的请求构建逻辑。
"""

from __future__ import annotations

import argparse
import logging
logger = logging.getLogger("funasr_ui")
import importlib
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
import subprocess
import importlib.util

import numpy as np
import requests

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
OPENAI_API_DIR = CURRENT_DIR.parent / "openai_api"
if str(OPENAI_API_DIR) not in sys.path:
    sys.path.insert(0, str(OPENAI_API_DIR))
PROJECT_ROOT = CURRENT_DIR.parent.parent

from app_utils import (
    CAPABILITY_FILTER_CHOICES,
    DEFAULT_DIARIZATION_MODEL,
    DEFAULT_EMOTION_MODEL,
    DEFAULT_MODEL,
    DEFAULT_STREAMING_MODEL,
    MEDIA_FILE_SUFFIXES,
    build_request_fields,
    build_known_model_choices,
    filter_asr_model_choices,
    choose_default_diarization_model,
    choose_default_emotion_model,
    render_capability_target_markdown,
    render_model_capability_markdown,
    render_service_overview_markdown,
    choose_default_model,
    choose_default_streaming_model,
    ensure_dropdown_choices,
    filter_diarization_model_choices,
    filter_emotion_model_choices,
    filter_streaming_model_choices,
    initialize_batch_results,
    is_binary_response_format,
    is_video_file,
    normalize_uploaded_paths,
    output_filename_for_format,
    parse_model_choices,
    summarize_batch_results,
    summarize_model_status,
)
import renderers as diarization_renderers

# 精细转录模块：音频前处理 + 精细转录管线
from fine_transcription.audio_processor import (
    process_audio as preprocess_audio,
    get_audio_info,
    format_audio_info,
)
from fine_transcription.scene_templates import SCENE_CHOICES, get_template
from fine_transcription.transcription_pipeline import (
    run_pipeline, run_pipeline_streaming, format_transcript_text, format_summary_display, export_result,
)
from fine_transcription.audio_sync_js import (
    get_audio_sync_html,
    get_markmap_html,
    json_for_inline_script,
)
from fine_transcription.llm_config import get_llm_choices, get_default_llm_value, get_llm_by_value
from workflow_ui import (
    build_workflow_config,
    render_workflow_event_panel,
    render_workflow_events,
)

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_PREVIEW_FORMAT = "txt"
PREVIEW_FORMAT_CHOICES = ["json", "txt", "srt", "vtt", "tsv"]
DEFAULT_BATCH_RESPONSE_FORMAT = "all"
RUNTIME_LOG_FILENAMES = ("funasr-api.log", "funasr-ui.log", "funasr-single-window.log")
VALID_UI_MODEL_HUBS = {"ms", "modelscope", "hf", "huggingface"}

PREVIEW_MAX_CHARS = 8000
RAW_JSON_PREVIEW_MAX_CHARS = 12000
BATCH_ITEM_MESSAGE_MAX_CHARS = 240
STREAMING_PREVIEW_MAX_CHARS = 4000
STREAMING_UI_UPDATE_EVERY_CHUNKS = 3
STREAMING_UI_UPDATE_MIN_INTERVAL_S = 0.5
STREAMING_DISPLAY_MIN_LINE_CHARS = 16
STREAMING_DISPLAY_MAX_LINE_CHARS = 42
SYSTEM_MIC_DEPENDENCY_CANDIDATES = ("sounddevice", "pyaudio")
SYSTEM_MIC_SAMPLE_RATE = 16000
SYSTEM_MIC_CHANNELS = 1
SYSTEM_MIC_BYTES_PER_SAMPLE = 2
SYSTEM_MIC_STREAMS: dict[str, dict] = {}
SYSTEM_MIC_STREAMS_LOCK = threading.Lock()
SYSTEM_MIC_DEFAULT_DEVICE_VALUE = "__default__"
BATCH_RUNNING_STATUS_UPDATE_EVERY_ITEMS = 3


def get_default_model_hub_for_ui() -> str:
    """读取 WebUI 默认模型来源；空字符串表示沿用后端/模型默认。"""
    raw = os.environ.get("FUNASR_MODEL_HUB", "").strip().lower()
    if raw not in VALID_UI_MODEL_HUBS:
        return ""
    if raw == "modelscope":
        return "ms"
    if raw == "huggingface":
        return "hf"
    return raw


def truncate_tail_text(text: str, max_chars: int) -> str:
    text = str(text or "")
    if max_chars <= 0:
        return text
    if len(text) <= max_chars:
        return text
    return f"(已截断，完整内容请下载) {text[-max_chars:]}"


def limit_preview_text(text: str) -> str:
    return truncate_tail_text(text, PREVIEW_MAX_CHARS)


def limit_raw_json_preview(text: str) -> str:
    return truncate_tail_text(text, RAW_JSON_PREVIEW_MAX_CHARS)


def build_payload_preview(payload: dict, *, max_segments: int = 12) -> dict:
    text = str(payload.get("text", "") or "")
    segments = payload.get("segments") or []
    preview: dict = {
        "text": truncate_tail_text(text, PREVIEW_MAX_CHARS),
        "segment_count": len(segments) if isinstance(segments, list) else 0,
    }
    if isinstance(segments, list) and segments:
        tail = segments[-max_segments:] if max_segments > 0 else segments
        trimmed = []
        for seg in tail:
            if not isinstance(seg, dict):
                continue
            trimmed.append(
                {
                    "start": seg.get("start", 0.0),
                    "end": seg.get("end", 0.0),
                    "text": truncate_tail_text(str(seg.get("text", "") or ""), 200),
                    "speaker": seg.get("speaker", None),
                }
            )
        preview["segments_tail"] = trimmed
    for key in ("language", "duration", "model"):
        if key in payload:
            preview[key] = payload.get(key)
    return preview


# #region debug-point A:debug-report
def _dbg_report(
    *,
    hypothesis_id: str,
    msg: str,
    location: str,
    data: dict | None = None,
    trace_id: str | None = None,
    run_id: str = "pre-fix",
) -> None:
    try:
        env_path = PROJECT_ROOT / ".dbg" / "gradio-page-hung.env"
        url = "http://127.0.0.1:7777/event"
        session_id = "gradio-page-hung"
        try:
            content = env_path.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                if line.startswith("DEBUG_SERVER_URL="):
                    url = line.split("=", 1)[1].strip() or url
                elif line.startswith("DEBUG_SESSION_ID="):
                    session_id = line.split("=", 1)[1].strip() or session_id
        except Exception:
            pass
        payload = {
            "sessionId": session_id,
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "msg": f"[DEBUG] {msg}",
            "data": data or {},
            "traceId": trace_id,
            "ts": int(time.time() * 1000),
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        return


# #endregion


# #region debug-point E:browser-instrumentation
def _dbg_get_server_config() -> tuple[str, str]:
    env_path = PROJECT_ROOT / ".dbg" / "gradio-page-hung.env"
    url = "http://127.0.0.1:7777/event"
    session_id = "gradio-page-hung"
    try:
        content = env_path.read_text(encoding="utf-8", errors="replace")
        for line in content.splitlines():
            if line.startswith("DEBUG_SERVER_URL="):
                url = line.split("=", 1)[1].strip() or url
            elif line.startswith("DEBUG_SESSION_ID="):
                session_id = line.split("=", 1)[1].strip() or session_id
    except Exception:
        pass
    return url, session_id


def _dbg_browser_instrumentation_html() -> str:
    url, session_id = _dbg_get_server_config()
    payload_url = json.dumps(url, ensure_ascii=False)
    payload_session = json.dumps(session_id, ensure_ascii=False)
    return f"""
<script>
(() => {{
  const DEBUG_URL = {payload_url};
  const SESSION_ID = {payload_session};
  const RUN_ID = "pre-fix";
  const LOCATION = "browser";
  const send = (hypothesisId, msg, data) => {{
    try {{
      fetch(DEBUG_URL, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{
          sessionId: SESSION_ID,
          runId: RUN_ID,
          hypothesisId,
          location: LOCATION,
          msg: "[DEBUG] " + msg,
          data: data || {{}},
          ts: Date.now(),
        }}),
        keepalive: true,
      }});
    }} catch (e) {{}}
  }};
  window.addEventListener("error", (ev) => {{
    send("E", "window_error", {{
      message: ev && ev.message,
      filename: ev && ev.filename,
      lineno: ev && ev.lineno,
      colno: ev && ev.colno,
    }});
  }});
  window.addEventListener("unhandledrejection", (ev) => {{
    send("E", "unhandledrejection", {{
      reason: String(ev && ev.reason),
    }});
  }});
  const origFetch = window.fetch;
  window.fetch = function(input, init) {{
    const url = (typeof input === "string") ? input : (input && input.url) || "";
    const t0 = performance.now();
    return origFetch.apply(this, arguments).then((resp) => {{
      const ms = Math.round(performance.now() - t0);
      if (url.includes("/gradio_api/queue/")) {{
        send("F", "fetch_ok", {{ url, status: resp.status, ms }});
      }}
      return resp;
    }}).catch((err) => {{
      const ms = Math.round(performance.now() - t0);
      if (url.includes("/gradio_api/queue/")) {{
        send("F", "fetch_err", {{ url, err: String(err), ms }});
      }}
      throw err;
    }});
  }};
  send("E", "client_instrumentation_ready", {{}});
}})();
</script>
""".strip()


# #endregion


APP_CSS = """
.pat-media-preview {
  max-width: 100%;
}
.pat-media-preview video {
  width: 100%;
  max-height: 280px;
  object-fit: contain;
}
.pat-compact-markdown h3 {
  margin: 8px 0 6px;
}
.pat-compact-markdown p {
  margin: 0;
}
.pat-placeholder-box {
  border: 1px dashed #c7cfdd;
  border-radius: 10px;
  padding: 12px 14px;
  background: #fafbfc;
}
/* 放大麦克风波形显示幅度 */
#pat-stream-microphone .waveform-container,
#pat-stream-microphone canvas {
  transform: scaleY(3);
  transform-origin: center center;
}

/* 修复 Gradio Audio 组件滚动条遮挡时间显示 */
.gr-audio audio-container,
.gr-audio .audio-container,
.audio-container {
    overflow: visible !important;
    padding-bottom: 18px !important;
}

.gr-audio .time,
.gr-audio .time-wrap,
.audio-container .time {
    z-index: 10 !important;
    position: relative !important;
}

.gr-audio .slider,
.gr-audio input[type="range"] {
    z-index: 1 !important;
}

.ft-audio-player .audio-container {
    padding-bottom: 20px !important;
}
"""


def normalize_timeout(timeout: float | None) -> float | None:
    """把超时值统一归一；小于等于 0 时视为不设超时。"""
    if timeout is None:
        return None
    try:
        normalized = float(timeout)
    except (TypeError, ValueError):
        return None
    if normalized <= 0:
        return None
    return normalized


def open_url(target, timeout: float | None):
    """按当前配置打开 URL；当 timeout<=0 时不传超时参数。"""
    normalized_timeout = normalize_timeout(timeout)
    if normalized_timeout is None:
        return urllib.request.urlopen(target)
    return urllib.request.urlopen(target, timeout=normalized_timeout)


def read_runtime_logs(
    max_lines: int = 120,
    *,
    max_bytes: int = 256 * 1024,
    max_section_chars: int = 8000,
) -> str:
    """读取当前工程根目录下的运行日志，便于直接在 UI 中查看。"""
    max_lines = max(0, int(max_lines))
    max_bytes = max(0, int(max_bytes))
    max_section_chars = max(0, int(max_section_chars))

    def read_tail_lines(log_path: Path) -> list[str]:
        try:
            with log_path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                read_size = min(size, max_bytes) if max_bytes > 0 else size
                if read_size <= 0:
                    return []
                handle.seek(-read_size, os.SEEK_END)
                chunk = handle.read(read_size)
        except OSError as error:
            return [f"读取失败：{error}"]

        text = chunk.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if max_bytes > 0 and size > read_size and lines:
            lines = lines[1:]
        if max_lines > 0:
            lines = lines[-max_lines:]
        return lines

    sections: list[str] = []
    for file_name in RUNTIME_LOG_FILENAMES:
        log_path = PROJECT_ROOT / file_name
        if not log_path.exists():
            continue
        lines = read_tail_lines(log_path)
        body = "\n".join(lines).strip()
        if not body:
            body = "(空日志)"
        if max_section_chars > 0 and len(body) > max_section_chars:
            body = body[-max_section_chars:]
        sections.append(f"=== {file_name} ===\n{body}")
    if not sections:
        return "未找到运行日志。建议使用 `\"FunASR_pat.bat\"` 启动，这样 API / UI 输出会同步显示在这里。"
    return "\n\n".join(sections)


def read_runtime_logs_ui(max_lines: int, max_bytes_kb: int, max_section_chars: int) -> str:
    # #region debug-point B:runtime-logs
    global _RUNTIME_LOG_TICK_COUNTER
    _RUNTIME_LOG_TICK_COUNTER = int(globals().get("_RUNTIME_LOG_TICK_COUNTER", 0)) + 1
    # #endregion
    text = read_runtime_logs(
        max_lines=int(max_lines),
        max_bytes=int(max_bytes_kb) * 1024,
        max_section_chars=int(max_section_chars),
    )
    # #region debug-point B:runtime-logs-report
    try:
        if _RUNTIME_LOG_TICK_COUNTER % 10 == 1:
            _dbg_report(
                hypothesis_id="B",
                msg="runtime_logs_tick",
                location="pat_funasr_webui/gradio_app.py:read_runtime_logs_ui",
                data={
                    "max_lines": int(max_lines),
                    "max_kb": int(max_bytes_kb),
                    "max_section_chars": int(max_section_chars),
                    "len": len(text),
                    "tick": int(_RUNTIME_LOG_TICK_COUNTER),
                },
            )
    except Exception:
        pass
    # #endregion
    return text


def read_runtime_logs_ui_guard(enabled: bool, max_lines: int, max_bytes_kb: int, max_section_chars: int):
    if not enabled:
        try:
            import gradio as gr
        except Exception:
            return None
        return gr.update()
    return read_runtime_logs_ui(max_lines, max_bytes_kb, max_section_chars)


def build_preview_file_state(exports: dict[str, str]) -> str:
    """把各格式导出文件路径保存到前端状态，便于切换预览格式时直接读取。"""
    return json.dumps({"exports": exports}, ensure_ascii=False)


def read_preview_text_from_state(preview_format: str, preview_state_json: str) -> str | None:
    """从前端状态中读取指定格式的预览文本；兼容旧状态时返回 None。"""
    try:
        state = json.loads(preview_state_json or "{}")
    except Exception:
        return None
    if not isinstance(state, dict):
        return None
    exports = state.get("exports")
    if not isinstance(exports, dict):
        return None
    target_path = exports.get(str(preview_format or DEFAULT_PREVIEW_FORMAT))
    if not target_path:
        return None
    try:
        content = Path(str(target_path)).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    if str(preview_format or DEFAULT_PREVIEW_FORMAT) == "json":
        return limit_raw_json_preview(content)
    return limit_preview_text(content)


def build_runtime_logs_archive() -> str | None:
    archive_path = Path(tempfile.gettempdir()) / f"pat-funasr-logs-{uuid.uuid4().hex}.zip"
    candidates = []
    for file_name in RUNTIME_LOG_FILENAMES:
        log_path = PROJECT_ROOT / file_name
        if log_path.exists():
            candidates.append(log_path)
    if not candidates:
        return None
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for log_path in candidates:
            zf.write(log_path, arcname=log_path.name)
    return str(archive_path)


def request_json(url: str, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with open_url(request, timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, timeout: float) -> dict:
    """发送空 JSON POST 请求，用于触发后端模型预加载等控制 API。"""
    request = urllib.request.Request(
        url,
        data=b"",
        method="POST",
        headers={"Accept": "application/json", "Content-Length": "0"},
    )
    with open_url(request, timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def post_json_payload(url: str, payload: dict, timeout: float) -> dict:
    """发送 JSON 请求并返回 JSON；用于工作流预校验。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with open_url(request, timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def submit_workflow_job(base_url: str, audio_path: str, config: dict, timeout: float) -> dict:
    """以流式文件句柄提交工作流，避免前端一次性读取完整媒体。"""
    with Path(audio_path).open("rb") as audio_stream:
        response = requests.post(
            f"{base_url.rstrip('/')}/v1/funasr/workflows",
            files={"file": (Path(audio_path).name, audio_stream, "application/octet-stream")},
            data={"workflow": json.dumps(config, ensure_ascii=False)},
            timeout=max(float(timeout), 30.0),
        )
    if not response.ok:
        raise RuntimeError(f"工作流提交失败({response.status_code})：{response.text[:500]}")
    return response.json()


def build_workflow_downloads(
    base_url: str,
    job_id: str,
    result: dict,
    timeout: float,
) -> dict[str, str | None]:
    """通过后端产物端点下载文件，兼容前后端不在同一主机。"""
    outputs: dict[str, str | None] = {key: None for key in ("json", "txt", "srt", "vtt", "tsv", "all")}
    artifact_paths: list[Path] = []
    download_root = Path(tempfile.gettempdir()) / f"pat-funasr-workflow-{uuid.uuid4().hex}"
    download_root.mkdir(parents=True, exist_ok=True)
    for artifact in result.get("artifacts") or []:
        name = str(artifact.get("name") or "")
        if not name or Path(name).name != name:
            continue
        url = (
            f"{base_url.rstrip('/')}/v1/funasr/workflows/"
            f"{urllib.parse.quote(job_id)}/artifacts/{urllib.parse.quote(name)}"
        )
        path = download_root / name
        response = requests.get(url, stream=True, timeout=max(float(timeout), 30.0))
        if not response.ok:
            raise RuntimeError(f"产物下载失败({response.status_code})：{name}")
        with path.open("wb") as output_stream:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    output_stream.write(chunk)
        artifact_paths.append(path)
        name = path.name.lower()
        if name.startswith("transcript."):
            suffix = path.suffix.lower().lstrip(".")
            if suffix in outputs:
                outputs[suffix] = str(path)
    if artifact_paths:
        archive_path = download_root / "workflow-artifacts.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in artifact_paths:
                archive.write(path, arcname=path.name)
        outputs["all"] = str(archive_path)
    return outputs


def multipart_body(audio_path: Path, fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = f"----funasr-gradio-{uuid.uuid4().hex}"
    content_type = mimetypes.guess_type(str(audio_path))[0] or "application/octet-stream"
    parts: list[bytes] = []

    def add_text(name: str, value: str) -> None:
        parts.append(
            (
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                f"{value}\r\n"
            ).encode("utf-8")
        )

    parts.append(
        (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"file\"; filename=\"{audio_path.name}\"\r\n"
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(audio_path.read_bytes())
    parts.append(b"\r\n")
    for field_name, field_value in fields.items():
        add_text(field_name, field_value)
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), boundary


def multipart_body_bytes(filename: str, payload: bytes, fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = f"----funasr-gradio-{uuid.uuid4().hex}"
    parts: list[bytes] = []

    def add_text(name: str, value: str) -> None:
        parts.append(
            (
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                f"{value}\r\n"
            ).encode("utf-8")
        )

    parts.append(
        (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(payload)
    parts.append(b"\r\n")
    for field_name, field_value in fields.items():
        add_text(field_name, field_value)
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), boundary


def parse_chunk_size_text(raw: str) -> list[int]:
    raw = (raw or "").strip()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError("chunk_size 必须是 3 个整数，例如 0,10,5")
    values = [int(p) for p in parts]
    if any(v < 0 for v in values):
        raise ValueError("chunk_size 不允许为负数")
    return values


def post_streaming_chunk(
    *,
    base_url: str,
    timeout: float,
    chunk_bytes: bytes,
    model: str,
    session_id: str,
    reset: bool,
    is_final: bool,
    chunk_size: str,
    encoder_chunk_look_back: int,
    decoder_chunk_look_back: int,
) -> dict:
    base_url = base_url.rstrip("/")
    fields = {
        "model": model,
        "session_id": session_id,
        "reset": "true" if reset else "false",
        "is_final": "true" if is_final else "false",
        "chunk_size": chunk_size,
        "encoder_chunk_look_back": str(int(encoder_chunk_look_back)),
        "decoder_chunk_look_back": str(int(decoder_chunk_look_back)),
    }
    body, boundary = multipart_body_bytes("chunk.pcm", chunk_bytes, fields)
    request = urllib.request.Request(
        f"{base_url}/v1/funasr/streaming",
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )
    with open_url(request, timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def ensure_streaming_model_ready(base_url: str, model: str, timeout: float) -> str:
    """开始 Mic 录制前确认后端模型已加载，未加载则主动预加载。"""
    normalized_base_url = str(base_url or DEFAULT_BASE_URL).rstrip("/")
    model_name = str(model or "").strip()
    if not model_name:
        raise ValueError("未选择流式识别模型。")
    encoded_model = urllib.parse.quote(model_name, safe="")
    status = request_json(f"{normalized_base_url}/v1/models/{encoded_model}/status", timeout)
    if status.get("ready"):
        return f"模型 {model_name} 已就绪，麦克风录制已开始。"

    loaded = post_json(f"{normalized_base_url}/v1/models/{encoded_model}/load", timeout)
    if not loaded.get("ready"):
        state = loaded.get("state") or status.get("state") or "unknown"
        error = loaded.get("error") or status.get("error") or ""
        detail = f"：{error}" if error else ""
        raise RuntimeError(f"模型 {model_name} 未就绪，状态 {state}{detail}")
    return f"模型 {model_name} 已就绪，麦克风录制已开始。"


def format_streaming_text_for_display(text: str) -> str:
    """把流式累计文本整理为单段文本，避免短句被主动拆行。"""
    cleaned = re.sub(r"[\r\n|]+", " ", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip()


def build_streaming_download_file(text: str) -> str:
    """生成流式识别文本下载文件；文本文件按项目规则使用 UTF-8 BOM。"""
    output_path = Path(tempfile.gettempdir()) / f"pat-funasr-streaming-{uuid.uuid4().hex}.txt"
    output_path.write_text(str(text or ""), encoding="utf-8-sig", newline="\r\n")
    return str(output_path)


def numpy_audio_to_pcm_bytes(audio) -> bytes:
    """把 Gradio 麦克风 numpy 音频转成 16kHz mono int16 PCM。"""
    if audio is None:
        return b""
    sample_rate, data = audio
    array = np.asarray(data)
    if array.size == 0:
        return b""
    source_is_float = array.dtype.kind == "f"
    array = array.astype(np.float32, copy=False)
    if array.ndim > 1:
        array = array.mean(axis=1)
    if source_is_float:
        array = np.clip(array, -1.0, 1.0) * 32767.0

    source_rate = int(sample_rate or 16000)
    if source_rate != 16000 and len(array) > 1:
        duration = len(array) / float(source_rate)
        target_len = max(1, int(duration * 16000))
        source_x = np.linspace(0.0, duration, num=len(array), endpoint=False)
        target_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
        array = np.interp(target_x, source_x, array).astype(np.float32)

    return np.clip(array, -32768, 32767).astype(np.int16).tobytes()


def _build_signal_bar(peak: float, bar_width: int = 20) -> str:
    """根据峰值构建简易 ASCII 音量条，峰值 0~1 映射到 bar_width 个字符。"""
    filled = int(min(peak, 1.0) * bar_width)
    empty = bar_width - filled
    # 使用 █ 表示已填充，░ 表示空白
    return f"[{'█' * filled}{'░' * empty}]"


def describe_microphone_signal(audio) -> str:
    """返回麦克风输入的可读信号摘要，用于判断浏览器是否真正收音。"""
    if audio is None:
        return "没有收到麦克风音频帧。请确认页面录制控件正在运行，浏览器输入设备为系统默认麦克风。"
    try:
        sample_rate, data = audio
        array = np.asarray(data)
        if array.size == 0:
            return "收到空麦克风音频块。请检查输入设备或浏览器权限。"
        source_is_float = array.dtype.kind == "f"
        values = array.astype(np.float32, copy=False)
        if values.ndim > 1:
            values = values.mean(axis=1)
        if not source_is_float:
            values = values / 32768.0
        peak = float(np.max(np.abs(values)))
        rms = float(np.sqrt(np.mean(values * values)))
        peak_pct = peak * 100
        rms_pct = rms * 100
        bar = _build_signal_bar(peak)
        # 降低阈值：peak >= 0.001 或 rms >= 0.0003 即视为有信号
        signal_tag = "✓" if peak >= 0.001 or rms >= 0.0003 else "⚠静音"
        return (
            f"{bar} 峰值：{peak_pct:.1f}% | RMS：{rms_pct:.1f}% {signal_tag}\n"
            f"采样率：{int(sample_rate)}Hz | 样本数：{array.shape[0]} | dtype：{array.dtype}"
        )
    except Exception as exc:
        return f"麦克风信号解析失败：{exc}"


def get_system_microphone_runtime_status() -> str:
    """说明当前 Mic 采集链路是否具备官方 examples 式本机采集条件。"""
    available = [
        name
        for name in SYSTEM_MIC_DEPENDENCY_CANDIDATES
        if importlib.util.find_spec(name) is not None
    ]
    if available:
        return (
            "Mic 采集链路：已检测到本机采集库 "
            f"{', '.join(available)}；当前页面使用系统输入设备采集，并转为 PCM16/16k 流式分片。"
        )
    return (
        "Mic 采集链路：当前未安装 sounddevice/pyaudio。"
        "页面先使用浏览器默认麦克风采集；官方 examples 的系统默认麦克风直采需要新增其中一个依赖。"
    )


def describe_pcm_signal(pcm_bytes: bytes, sample_rate: int = SYSTEM_MIC_SAMPLE_RATE) -> str:
    """返回 PCM16 音频块的信号摘要。"""
    if not pcm_bytes:
        return "没有收到系统麦克风 PCM 音频帧。"
    try:
        array = np.frombuffer(pcm_bytes, dtype=np.int16)
        if array.size == 0:
            return "收到空系统麦克风 PCM 音频帧。"
        peak = int(np.max(np.abs(array.astype(np.int32))))
        peak_pct = peak / 32768.0 * 100
        bar = _build_signal_bar(peak / 32768.0)
        signal_tag = "✓" if peak > 1 else "⚠静音"
        return (
            f"{bar} 峰值：{peak_pct:.1f}% {signal_tag}\n"
            f"采样率：{sample_rate}Hz | 样本数：{array.size}"
        )
    except Exception as exc:
        return f"系统麦克风 PCM 解析失败：{exc}"


def convert_system_mic_pcm_to_funasr_pcm(
    pcm_bytes: bytes,
    source_rate: int,
    source_channels: int,
) -> bytes:
    """把系统麦克风设备格式转换为 FunASR streaming 需要的 16k mono int16 PCM。"""
    if not pcm_bytes:
        return b""
    array = np.frombuffer(pcm_bytes, dtype=np.int16)
    if array.size == 0:
        return b""
    channels = max(1, int(source_channels or 1))
    if channels > 1:
        usable = (array.size // channels) * channels
        if usable <= 0:
            return b""
        array = array[:usable].reshape(-1, channels).mean(axis=1)
    array = array.astype(np.float32, copy=False)

    rate = int(source_rate or SYSTEM_MIC_SAMPLE_RATE)
    if rate != SYSTEM_MIC_SAMPLE_RATE and array.size > 1:
        duration = array.size / float(rate)
        target_len = max(1, int(duration * SYSTEM_MIC_SAMPLE_RATE))
        source_x = np.linspace(0.0, duration, num=array.size, endpoint=False)
        target_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
        array = np.interp(target_x, source_x, array).astype(np.float32)
    return np.clip(array, -32768, 32767).astype(np.int16).tobytes()


def system_microphone_frames_per_buffer(chunk_size: str) -> int:
    """按 FunASR 官方 streaming chunk_size 计算每次采集的采样帧数。"""
    parsed = parse_chunk_size_text(chunk_size)
    return max(960, int(parsed[1]) * 960)


def load_pyaudio_module():
    """延迟加载 PyAudio，便于测试和缺依赖时给出前台错误。"""
    return importlib.import_module("pyaudio")


def list_system_microphone_device_choices() -> list[tuple[str, str]]:
    """枚举系统输入设备；第一项始终是系统默认输入设备。"""
    choices = [("系统默认输入设备", SYSTEM_MIC_DEFAULT_DEVICE_VALUE)]
    try:
        pyaudio_module = load_pyaudio_module()
        audio_api = pyaudio_module.PyAudio()
        try:
            default_index = None
            try:
                default_index = int(audio_api.get_default_input_device_info().get("index"))
            except Exception:
                default_index = None
            for index in range(audio_api.get_device_count()):
                info = audio_api.get_device_info_by_index(index)
                if int(info.get("maxInputChannels", 0) or 0) <= 0:
                    continue
                name = str(info.get("name", f"Input {index}"))
                rate = int(float(info.get("defaultSampleRate") or 0))
                default_suffix = " / 当前默认" if default_index == index else ""
                choices.append((f"{index} - {name} ({rate}Hz){default_suffix}", str(index)))
        finally:
            audio_api.terminate()
    except Exception:
        return choices
    return choices


def refresh_system_microphone_device_dropdown():
    """刷新系统输入设备列表，并恢复为系统默认输入设备。"""
    import gradio as gr

    return gr.update(
        choices=list_system_microphone_device_choices(),
        value=SYSTEM_MIC_DEFAULT_DEVICE_VALUE,
    )


def resolve_system_microphone_device_info(audio_api, device_value: str | None) -> tuple[dict, int | None]:
    """解析麦克风设备选择；默认项使用系统默认输入设备。"""
    value = str(device_value or SYSTEM_MIC_DEFAULT_DEVICE_VALUE)
    if value == SYSTEM_MIC_DEFAULT_DEVICE_VALUE:
        return audio_api.get_default_input_device_info(), None
    index = int(value)
    return audio_api.get_device_info_by_index(index), index


def _update_system_microphone_session(session_id: str, **updates) -> None:
    """线程安全更新系统麦克风会话状态。"""
    with SYSTEM_MIC_STREAMS_LOCK:
        session = SYSTEM_MIC_STREAMS.get(session_id)
        if session is not None:
            session.update(updates)


def run_system_microphone_capture(
    session_id: str,
    base_url: str,
    model: str,
    timeout: float,
    device_value: str | None,
    chunk_size: str,
    encoder_chunk_look_back: int,
    decoder_chunk_look_back: int,
) -> None:
    """后台采集系统默认麦克风，并按 FunASR streaming 端点发送 PCM16/16k 分片。"""
    audio_api = None
    stream = None
    sent = 0
    try:
        pyaudio_module = load_pyaudio_module()
        audio_api = pyaudio_module.PyAudio()
        device_info, input_device_index = resolve_system_microphone_device_info(audio_api, device_value)
        source_rate = int(float(device_info.get("defaultSampleRate") or SYSTEM_MIC_SAMPLE_RATE))
        source_channels = min(2, max(1, int(device_info.get("maxInputChannels") or SYSTEM_MIC_CHANNELS)))
        target_frames_per_buffer = system_microphone_frames_per_buffer(chunk_size)
        frames_per_buffer = max(256, int(target_frames_per_buffer * source_rate / SYSTEM_MIC_SAMPLE_RATE))
        stream = audio_api.open(
            format=pyaudio_module.paInt16,
            channels=source_channels,
            rate=source_rate,
            input=True,
            input_device_index=input_device_index,
            frames_per_buffer=frames_per_buffer,
        )
        device_name = str(device_info.get("name", "系统默认麦克风"))
        _update_system_microphone_session(
            session_id,
            status=f"{device_name} 已开始录制，正在实时识别。",
            signal=f"设备采样率：{source_rate}Hz；声道：{source_channels}；等待首个音频帧...",
        )
        while True:
            with SYSTEM_MIC_STREAMS_LOCK:
                session = SYSTEM_MIC_STREAMS.get(session_id)
                stop_event = session.get("stop_event") if session else None
            if stop_event is None or stop_event.is_set():
                break

            raw_chunk_bytes = stream.read(frames_per_buffer, exception_on_overflow=False)
            if not raw_chunk_bytes:
                _update_system_microphone_session(session_id, signal="系统麦克风返回空音频帧。")
                continue

            signal_status = describe_pcm_signal(raw_chunk_bytes, sample_rate=source_rate)
            chunk_bytes = convert_system_mic_pcm_to_funasr_pcm(
                raw_chunk_bytes,
                source_rate=source_rate,
                source_channels=source_channels,
            )
            if not chunk_bytes:
                _update_system_microphone_session(session_id, signal="系统麦克风音频转换后为空。")
                continue
            payload = post_streaming_chunk(
                base_url=base_url,
                timeout=timeout,
                chunk_bytes=chunk_bytes,
                model=model,
                session_id=session_id,
                reset=sent == 0,
                is_final=False,
                chunk_size=chunk_size,
                encoder_chunk_look_back=int(encoder_chunk_look_back),
                decoder_chunk_look_back=int(decoder_chunk_look_back),
            )
            sent += 1
            full_text = str(payload.get("full_text", "") or "")
            _update_system_microphone_session(
                session_id,
                sent=sent,
                full_text=full_text,
                signal=signal_status,
                status=f"系统麦克风实时识别中，已发送分片：{sent}",
            )
    except Exception as exc:
        _update_system_microphone_session(session_id, status=f"系统麦克风识别失败：{exc}", active=False)
        return
    finally:
        try:
            if stream is not None:
                stream.stop_stream()
                stream.close()
        except Exception:
            pass
        try:
            if audio_api is not None:
                audio_api.terminate()
        except Exception:
            pass
        with SYSTEM_MIC_STREAMS_LOCK:
            session = SYSTEM_MIC_STREAMS.get(session_id)
            if session is not None:
                session["active"] = False
                if "失败" not in str(session.get("status", "")):
                    session["status"] = f"系统麦克风录制已停止；已发送分片：{sent}。"


def toggle_system_microphone_stream(
    session_id: str | None,
    base_url: str,
    model: str,
    timeout: float,
    device_value: str | None,
    chunk_size: str,
    encoder_chunk_look_back: int,
    decoder_chunk_look_back: int,
):
    """单按钮切换系统默认麦克风录制与流式识别。"""
    import gradio as gr

    current_id = str(session_id or "")
    if current_id:
        with SYSTEM_MIC_STREAMS_LOCK:
            session = SYSTEM_MIC_STREAMS.get(current_id)
            stop_event = session.get("stop_event") if session else None
        if stop_event is not None:
            stop_event.set()
        return (
            current_id,
            "正在停止系统麦克风录制...",
            gr.update(value="开始录制并识别", variant="primary"),
        )

    try:
        parse_chunk_size_text(chunk_size)
        load_pyaudio_module()
    except Exception as exc:
        return "", f"系统麦克风启动失败：{exc}", gr.update(value="开始录制并识别", variant="primary")

    new_id = uuid.uuid4().hex
    stop_event = threading.Event()
    session = {
        "active": True,
        "stop_event": stop_event,
        "full_text": "",
        "status": "正在打开系统默认麦克风...",
        "signal": "等待系统麦克风音频帧...",
        "sent": 0,
    }
    with SYSTEM_MIC_STREAMS_LOCK:
        SYSTEM_MIC_STREAMS[new_id] = session

    thread = threading.Thread(
        target=run_system_microphone_capture,
        args=(
            new_id,
            base_url,
            model,
            float(timeout),
            device_value,
            chunk_size,
            int(encoder_chunk_look_back),
            int(decoder_chunk_look_back),
        ),
        daemon=True,
        name=f"pat-funasr-system-mic-{new_id[:8]}",
    )
    session["thread"] = thread
    thread.start()
    return new_id, "系统麦克风录制已启动，正在等待音频帧...", gr.update(value="停止录制并识别", variant="stop")


def start_system_microphone_stream(
    base_url: str,
    model: str,
    timeout: float,
    device_value: str | None,
    chunk_size: str,
    encoder_chunk_look_back: int,
    decoder_chunk_look_back: int,
) -> tuple[str, str]:
    """由 Gradio 原生麦克风控件开始录制事件触发系统麦克风识别。"""
    session_id, status, _button_update = toggle_system_microphone_stream(
        "",
        base_url,
        model,
        timeout,
        device_value,
        chunk_size,
        encoder_chunk_look_back,
        decoder_chunk_look_back,
    )
    return session_id, status


def stop_system_microphone_stream(session_id: str | None) -> tuple[str, str]:
    """由 Gradio 原生麦克风控件停止录制事件触发系统麦克风停止。"""
    current_id = str(session_id or "")
    if not current_id:
        return "", "系统麦克风录制已停止。"
    with SYSTEM_MIC_STREAMS_LOCK:
        session = SYSTEM_MIC_STREAMS.get(current_id)
        stop_event = session.get("stop_event") if session else None
    if stop_event is not None:
        stop_event.set()
    return current_id, "正在停止系统麦克风录制..."


def poll_system_microphone_stream(session_id: str | None):
    """轮询后台系统麦克风识别状态并刷新前台。"""
    import gradio as gr

    current_id = str(session_id or "")
    if not current_id:
        return gr.update(), gr.update(), gr.update(), ""
    with SYSTEM_MIC_STREAMS_LOCK:
        session = dict(SYSTEM_MIC_STREAMS.get(current_id) or {})
    if not session:
        return gr.update(), "系统麦克风会话不存在或已清理。", gr.update(), ""

    transcript = format_streaming_preview_text(str(session.get("full_text", "")), final_flag=not bool(session.get("active")))
    status = str(session.get("status", ""))
    signal = str(session.get("signal", ""))
    if session.get("active"):
        return transcript, status, signal, current_id
    with SYSTEM_MIC_STREAMS_LOCK:
        SYSTEM_MIC_STREAMS.pop(current_id, None)
    return transcript, status, signal, ""


def init_microphone_streaming_state(base_url: str, model: str, timeout: float) -> tuple[dict, str]:
    """初始化浏览器麦克风流式识别状态。"""
    state = {
        "session_id": uuid.uuid4().hex,
        "model": model,
        "full_text": "",
        "last_chunk_bytes": b"",
        "sent": 0,
        "started": False,
        "model_ready": False,
    }
    try:
        status = ensure_streaming_model_ready(base_url, model, timeout)
        state["started"] = True
        state["model_ready"] = True
        state["status"] = status
        return state, status
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        status = f"模型加载失败：HTTP {error.code}: {detail}"
    except Exception as error:
        status = f"模型加载失败：{error}"
    state["status"] = status
    return state, status


def finish_microphone_streaming_state(
    state: dict | None,
    base_url: str,
    model: str,
    timeout: float,
    chunk_size: str,
    encoder_chunk_look_back: int,
    decoder_chunk_look_back: int,
) -> tuple[dict, str, str]:
    """停止录制时结束麦克风识别状态。"""
    next_state = dict(state or {})
    next_state["started"] = False
    sent = int(next_state.get("sent", 0) or 0)
    full_text = str(next_state.get("full_text", "") or "")
    last_chunk_bytes = next_state.get("last_chunk_bytes") or b""
    if sent and last_chunk_bytes:
        try:
            payload = post_streaming_chunk(
                base_url=base_url,
                timeout=timeout,
                chunk_bytes=last_chunk_bytes,
                model=model,
                session_id=str(next_state["session_id"]),
                reset=False,
                is_final=True,
                chunk_size=chunk_size,
                encoder_chunk_look_back=int(encoder_chunk_look_back),
                decoder_chunk_look_back=int(decoder_chunk_look_back),
            )
            next_state["full_text"] = str(payload.get("full_text", full_text) or "")
            full_text = str(next_state["full_text"])
            preview = format_streaming_preview_text(full_text, final_flag=True)
            return next_state, f"已停止录制，已发送最终分片；总分片：{sent + 1}。", preview
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            preview = format_streaming_preview_text(full_text, final_flag=True)
            return next_state, f"停止录制时最终分片失败：HTTP {error.code}: {detail}", preview
        except Exception as error:
            preview = format_streaming_preview_text(full_text, final_flag=True)
            return next_state, f"停止录制时最终分片失败：{error}", preview
    if sent:
        preview = format_streaming_preview_text(full_text, final_flag=True)
        return next_state, f"已停止录制，识别已结束；已发送分片：{sent}。", preview
    return next_state, "已停止录制，未收到可识别的麦克风音频帧。", format_streaming_preview_text(full_text, final_flag=True)


def stream_transcribe_microphone(
    audio,
    state: dict | None,
    base_url: str,
    model: str,
    timeout: float,
    chunk_size: str,
    encoder_chunk_look_back: int,
    decoder_chunk_look_back: int,
):
    """接收 Gradio 麦克风流式音频块，并转发到后端 streaming 端点。"""
    # Gradio 的流式音频输入要求每个分片同步返回；改成 yield 会被注册为生成式输出事件。
    state = dict(state or {})
    if not state.get("session_id") or state.get("model") != model:
        state, _ = init_microphone_streaming_state(base_url, model, timeout)
    signal_status = describe_microphone_signal(audio)
    if state.get("model_ready") is False:
        status = str(state.get("status") or "模型未就绪")
        if "失败" in status or "error" in status.lower():
            status += "。请检查后端 API 是否运行，或在服务页刷新模型列表。"
        return format_streaming_preview_text(state.get("full_text", ""), final_flag=False), f"{status}\n{signal_status}", state

    try:
        parse_chunk_size_text(chunk_size)
        chunk_bytes = numpy_audio_to_pcm_bytes(audio)
        if not chunk_bytes:
            return format_streaming_preview_text(state.get("full_text", ""), final_flag=False), f"等待麦克风音频...\n{signal_status}", state

        payload = post_streaming_chunk(
            base_url=base_url,
            timeout=timeout,
            chunk_bytes=chunk_bytes,
            model=model,
            session_id=str(state["session_id"]),
            reset=not bool(state.get("sent")),
            is_final=False,
            chunk_size=chunk_size,
            encoder_chunk_look_back=int(encoder_chunk_look_back),
            decoder_chunk_look_back=int(decoder_chunk_look_back),
        )
        state["sent"] = int(state.get("sent", 0)) + 1
        state["last_chunk_bytes"] = chunk_bytes
        state["full_text"] = str(payload.get("full_text", state.get("full_text", "")) or "")
        preview = format_streaming_preview_text(str(state.get("full_text", "")), final_flag=False)
        return preview, f"麦克风实时识别中，已发送分片：{state['sent']}\n{signal_status}", state
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        preview = format_streaming_preview_text(state.get("full_text", ""), final_flag=False)
        return preview, f"HTTP {error.code} from {error.url}: {detail}\n{signal_status}", state
    except Exception as error:
        preview = format_streaming_preview_text(state.get("full_text", ""), final_flag=False)
        return preview, f"麦克风流式识别失败：{error}\n{signal_status}", state


def stop_streaming_status() -> str:
    """停止按钮的轻量状态反馈；实际取消由 Gradio cancels 处理。"""
    return "已请求停止识别。"


def stream_transcribe_file(
    base_url: str,
    audio_path: str | None,
    model: str,
    timeout: float,
    chunk_size: str,
    encoder_chunk_look_back: int,
    decoder_chunk_look_back: int,
):
    if not audio_path:
        yield "", "请先上传或录制音频/视频文件。"
        return

    path = Path(audio_path)
    if is_video_file(path):
        extracted = extract_audio_from_video(str(path))
        if extracted:
            path = Path(extracted)
        else:
            yield "", "不支持的视频格式，仅支持音频文件(wav/mp3/m4a/flac/ogg)"
            return

    try:
        parsed = parse_chunk_size_text(chunk_size)
    except Exception as exc:
        yield "", f"chunk_size 解析失败：{exc}"
        return

    ffmpeg = get_ffmpeg_exe()
    if not ffmpeg:
        yield "", "未找到 ffmpeg，无法进行流式识别（需要用 ffmpeg 转 PCM）。"
        return

    chunk_stride_samples = parsed[1] * 960
    bytes_per_chunk = max(1, int(chunk_stride_samples) * 2)
    session_id = uuid.uuid4().hex

    cmd = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-vn",
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        "pipe:1",
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as exc:
        yield "", f"启动 ffmpeg 失败：{exc}"
        return

    try:
        stdout = proc.stdout
        if stdout is None:
            yield "", "ffmpeg 输出管道不可用"
            return

        current = stdout.read(bytes_per_chunk)
        if not current:
            stderr = b""
            if proc.stderr is not None:
                stderr = proc.stderr.read() or b""
            yield "", f"ffmpeg 未输出任何 PCM 数据：{stderr.decode('utf-8', errors='replace')}"
            return

        full_text = ""
        sent = 0
        first = True
        last_emit_time = 0.0
        while current:
            nxt = stdout.read(bytes_per_chunk)
            final_flag = not nxt
            try:
                payload = post_streaming_chunk(
                    base_url=base_url,
                    timeout=timeout,
                    chunk_bytes=current,
                    model=model,
                    session_id=session_id,
                    reset=first,
                    is_final=final_flag,
                    chunk_size=chunk_size,
                    encoder_chunk_look_back=encoder_chunk_look_back,
                    decoder_chunk_look_back=decoder_chunk_look_back,
                )
                first = False
                full_text = str(payload.get("full_text", full_text) or "")
                sent += 1
                preview_text = format_streaming_preview_text(full_text, final_flag=final_flag)
                now = time.monotonic()
                should_emit = (
                    final_flag
                    or sent == 1
                    or (
                        sent % STREAMING_UI_UPDATE_EVERY_CHUNKS == 0
                        and (now - last_emit_time) >= STREAMING_UI_UPDATE_MIN_INTERVAL_S
                    )
                )
                if should_emit:
                    last_emit_time = now
                    yield preview_text or full_text, f"已发送分片：{sent}，is_final={final_flag}"
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")
                preview_text = format_streaming_preview_text(full_text, final_flag=False)
                yield preview_text or full_text, f"HTTP {error.code} from {error.url}: {detail}"
                return
            except Exception as error:
                preview_text = format_streaming_preview_text(full_text, final_flag=False)
                yield preview_text or full_text, f"流式识别失败：{error}"
                return
            current = nxt
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass


def request_transcription_payload(
    base_url: str,
    audio_path: str | None,
    timeout: float,
    **request_fields,
) -> dict:
    """请求离线识别并返回结构化 JSON。"""
    if not audio_path:
        raise ValueError("上传或录制音频文件后再点击识别。")

    base_url = base_url.rstrip("/")
    path = Path(audio_path)
    if is_video_file(path):
        extracted = extract_audio_from_video(str(path))
        if extracted:
            path = Path(extracted)
        else:
            raise ValueError("不支持的视频格式，仅支持音频文件(wav/mp3/m4a/flac/ogg)")

    fields = build_request_fields(**request_fields)
    body, boundary = multipart_body(path, fields)
    request = urllib.request.Request(
        f"{base_url}/v1/audio/transcriptions",
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )
    with open_url(request, timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def transcribe_audio(
    base_url: str,
    audio_path: str | None,
    model: str,
    response_format: str,
    timeout: float,
    language: str | None = None,
    hotword: str | None = None,
    vad_preset: str | None = None,
    merge_vad: str | bool | None = None,
    use_itn: str | bool | None = None,
    merge_length_s: int | None = None,
    max_line_width: int | None = None,
    batch_size_s: int | None = None,
    batch_size_threshold_s: int | None = None,
    vad_max_single_segment_time: int | None = None,
    punc_mode: str | None = None,
    device: str | None = None,
    hub: str | None = None,
    disable_update: str | bool | None = None,
    ncpu: int | None = None,
    log_level: str | None = None,
    disable_pbar: str | bool | None = None,
) -> tuple[str, str, str | None]:
    if not audio_path:
        return "", "上传或录制音频文件后再点击识别。", None

    base_url = base_url.rstrip("/")
    path = Path(audio_path)
    if is_video_file(path):
        extracted = extract_audio_from_video(str(path))
        if extracted:
            path = Path(extracted)
        else:
            return "", "不支持的视频格式，仅支持音频文件(wav/mp3/m4a/flac/ogg)", None

    fields = build_request_fields(
        model=model,
        response_format=response_format,
        language=language,
        hotword=hotword,
        vad_preset=vad_preset,
        merge_vad=merge_vad,
        use_itn=use_itn,
        merge_length_s=merge_length_s,
        max_line_width=max_line_width,
        batch_size_s=batch_size_s,
        batch_size_threshold_s=batch_size_threshold_s,
        vad_max_single_segment_time=vad_max_single_segment_time,
        punc_mode=punc_mode,
        device=device,
        hub=hub,
        disable_update=disable_update,
        ncpu=ncpu,
        log_level=log_level,
        disable_pbar=disable_pbar,
    )
    body, boundary = multipart_body(path, fields)
    request = urllib.request.Request(
        f"{base_url}/v1/audio/transcriptions",
        data=body,
        method="POST",
        headers={
            "Accept": "application/octet-stream" if is_binary_response_format(response_format) else "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )
    with open_url(request, timeout) as response:
        raw_bytes = response.read()
        if is_binary_response_format(response_format):
            suffix_name = output_filename_for_format(response_format)
            output_path = Path(tempfile.gettempdir()) / f"pat-funasr-{uuid.uuid4().hex}-{suffix_name}"
            output_path.write_bytes(raw_bytes)
            if response_format == "all":
                preview_text = "已生成 output.zip（完整内容请下载查看）"
                return preview_text, preview_text, str(output_path)
            tail_bytes = raw_bytes
            max_tail_bytes = PREVIEW_MAX_CHARS * 4
            if max_tail_bytes > 0 and len(raw_bytes) > max_tail_bytes:
                tail_bytes = raw_bytes[-max_tail_bytes:]
            preview_text = limit_preview_text(tail_bytes.decode("utf-8", errors="replace"))
            return preview_text, preview_text, str(output_path)
        payload = json.loads(raw_bytes.decode("utf-8"))

    text = payload.get("text", "")
    return (
        limit_preview_text(text),
        json.dumps(build_payload_preview(payload), ensure_ascii=False, indent=2),
        None,
    )


def transcribe_audio_with_exports(
    base_url: str,
    audio_path: str | None,
    model: str,
    response_format: str,
    timeout: float,
    language: str | None = None,
    hotword: str | None = None,
    vad_preset: str | None = None,
    merge_vad: str | bool | None = None,
    use_itn: str | bool | None = None,
    merge_length_s: int | None = None,
    max_line_width: int | None = None,
    batch_size_s: int | None = None,
    batch_size_threshold_s: int | None = None,
    vad_max_single_segment_time: int | None = None,
    punc_mode: str | None = None,
    device: str | None = None,
    hub: str | None = None,
    disable_update: str | bool | None = None,
    ncpu: int | None = None,
    log_level: str | None = None,
    disable_pbar: str | bool | None = None,
) -> tuple[str, str, str | None, str | None, str | None, str | None, str | None, str | None]:
    """调用离线识别，并生成多格式导出文件。"""
    payload = request_transcription_payload(
        base_url=base_url,
        audio_path=audio_path,
        timeout=timeout,
        model=model,
        response_format="verbose_json",
        language=language,
        hotword=hotword,
        vad_preset=vad_preset,
        merge_vad=merge_vad,
        use_itn=use_itn,
        merge_length_s=merge_length_s,
        max_line_width=max_line_width,
        batch_size_s=batch_size_s,
        batch_size_threshold_s=batch_size_threshold_s,
        vad_max_single_segment_time=vad_max_single_segment_time,
        punc_mode=punc_mode,
        device=device,
        hub=hub,
        disable_update=disable_update,
        ncpu=ncpu,
        log_level=log_level,
        disable_pbar=disable_pbar,
    )
    exports = build_transcription_export_files(payload)
    payload_preview_json = build_preview_file_state(exports)
    return (
        limit_preview_text(render_transcription_preview(payload, response_format)),
        payload_preview_json,
        exports.get("json"),
        exports.get("txt"),
        exports.get("srt"),
        exports.get("vtt"),
        exports.get("tsv"),
        exports.get("all"),
    )


def check_service(base_url: str, timeout: float) -> str:
    base_url = base_url.rstrip("/")
    health = request_json(f"{base_url}/health", timeout)
    models = request_json(f"{base_url}/v1/models", timeout)
    runtime = request_json(f"{base_url}/v1/runtime/status", timeout)
    return limit_raw_json_preview(json.dumps({"health": health, "runtime": runtime, "models": models}, ensure_ascii=False, indent=2))


def build_runtime_panels(base_url: str, timeout: float) -> tuple[str, str]:
    """渲染运行资源与工作流队列两个独立面板。"""
    runtime = request_json(f"{base_url.rstrip('/')}/v1/runtime/status", timeout)
    resources = runtime.get("resources") or {}
    cpu = resources.get("cpu") or {}
    memory = resources.get("memory") or {}
    gpu = resources.get("gpu") or {}
    resource_lines = ["### 运行资源"]
    resource_lines.append(
        f"- CPU：{cpu.get('percent', 'unavailable')}% | 逻辑核心：{cpu.get('logical_count', 'unavailable')}"
        if cpu.get("available") else f"- CPU：不可用（{cpu.get('reason', 'unknown')}）"
    )
    resource_lines.append(
        f"- 内存：{memory.get('percent', 'unavailable')}% | 可用 {int(memory.get('available_bytes', 0)) // (1024 ** 2)} MiB"
        if memory.get("available") else f"- 内存：不可用（{memory.get('reason', 'unknown')}）"
    )
    resource_lines.append(
        f"- GPU：{gpu.get('device', '')} | 已分配 {int(gpu.get('allocated_bytes', 0)) // (1024 ** 2)} MiB / {int(gpu.get('total_bytes', 0)) // (1024 ** 2)} MiB"
        if gpu.get("available") else f"- GPU：不可用（{gpu.get('reason', 'unknown')}）"
    )
    models = runtime.get("models") or {}
    resource_lines.append(f"- 已加载模型：{', '.join(models.get('loaded') or []) or '无'}")

    queue = runtime.get("workflow_queue") or {}
    counts = queue.get("status_counts") or {}
    queue_lines = [
        "### 任务队列",
        f"- 总任务：{queue.get('total', 0)} | 活跃：{queue.get('active', 0)}",
        (
            f"- 排队 {counts.get('queued', 0)} / 运行 {counts.get('running', 0)} / "
            f"完成 {counts.get('completed', 0)} / 失败 {counts.get('failed', 0)} / 取消 {counts.get('cancelled', 0)}"
        ),
    ]
    return "\n".join(resource_lines), "\n".join(queue_lines)


def safe_build_runtime_panels(base_url: str, timeout: float) -> tuple[str, str]:
    try:
        return build_runtime_panels(base_url, timeout)
    except Exception as error:
        return f"### 运行资源\n\n加载失败：{error}", f"### 任务队列\n\n加载失败：{error}"


def build_service_dashboard_snapshot(base_url: str, timeout: float, capability_filter: str) -> tuple[str, str, str, str, str]:
    """生成服务页自动刷新所需的轻量快照。"""
    base_url = base_url.rstrip("/")
    _, status_text, models = fetch_model_choices(base_url, timeout)
    health = request_json(f"{base_url}/health", timeout)
    runtime = request_json(f"{base_url}/v1/runtime/status", timeout)
    raw_json = limit_raw_json_preview(json.dumps({"health": health, "runtime": runtime, "models": models}, ensure_ascii=False, indent=2))
    capability_markdown = render_model_capability_markdown(models, capability_filter=capability_filter)
    target_markdown = render_capability_target_markdown(models, capability_filter=capability_filter)
    overview_markdown = render_service_overview_markdown(
        models,
        base_url=base_url,
        capability_filter=capability_filter,
    )
    return status_text, raw_json, overview_markdown, capability_markdown, target_markdown


def check_service_and_capabilities(base_url: str, timeout: float, capability_filter: str) -> tuple[str, str, str]:
    """同时返回服务状态原始 JSON 与模型能力看板。"""
    _, raw_json, overview_markdown, capability_markdown, target_markdown = build_service_dashboard_snapshot(
        base_url,
        timeout,
        capability_filter,
    )
    return raw_json, overview_markdown, capability_markdown, target_markdown


def get_ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    import shutil

    return shutil.which("ffmpeg")


def extract_audio_from_video(video_path: str | None) -> str | None:
    if not video_path:
        return None
    ffmpeg = get_ffmpeg_exe()
    if not ffmpeg:
        return None
    wav_path = tempfile.mktemp(suffix=".wav")
    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", video_path, "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", wav_path],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return wav_path
    except Exception:
        try:
            os.unlink(wav_path)
        except Exception:
            pass
        return None


def safe_transcribe(
    base_url: str,
    audio_path: str | None,
    model: str,
    response_format: str,
    timeout: float,
    language: str | None,
    hotword: str | None,
    vad_preset: str | None,
    merge_vad: str | bool | None,
    use_itn: str | bool | None,
    merge_length_s: int | None,
    max_line_width: int | None,
    batch_size_s: int | None,
    batch_size_threshold_s: int | None,
    vad_max_single_segment_time: int | None,
    punc_mode: str | None,
    device: str | None,
    hub: str | None,
    disable_update: str | bool | None,
    ncpu: int | None,
    log_level: str | None,
    disable_pbar: str | bool | None,
) -> tuple[str, str, str | None]:
    try:
        return transcribe_audio(
            base_url=base_url,
            audio_path=audio_path,
            model=model,
            response_format=response_format,
            timeout=timeout,
            language=language,
            hotword=hotword,
            vad_preset=vad_preset,
            merge_vad=merge_vad,
            use_itn=use_itn,
            merge_length_s=merge_length_s,
            max_line_width=max_line_width,
            batch_size_s=batch_size_s,
            batch_size_threshold_s=batch_size_threshold_s,
            vad_max_single_segment_time=vad_max_single_segment_time,
            punc_mode=punc_mode,
            device=device,
            hub=hub,
            disable_update=disable_update,
            ncpu=ncpu,
            log_level=log_level,
            disable_pbar=disable_pbar,
        )
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        return "", f"HTTP {error.code} from {error.url}: {detail}", None
    except Exception as error:
        return "", f"Transcription failed: {error}", None


def safe_transcribe_with_exports(
    base_url: str,
    audio_path: str | None,
    model: str,
    preview_format: str,
    timeout: float,
    language: str | None,
    hotword: str | None,
    vad_preset: str | None,
    merge_vad: str | bool | None,
    use_itn: str | bool | None,
    merge_length_s: int | None,
    max_line_width: int | None,
    batch_size_s: int | None,
    batch_size_threshold_s: int | None,
    vad_max_single_segment_time: int | None,
    punc_mode: str | None,
    device: str | None,
    hub: str | None,
    disable_update: str | bool | None,
    ncpu: int | None,
    log_level: str | None,
    disable_pbar: str | bool | None,
) -> tuple[str, str, str | None, str | None, str | None, str | None, str | None, str | None]:
    """安全调用离线识别，并返回预览文本、原始 JSON 与全量下载文件。"""
    # #region debug-point A:offline-entry
    trace_id = uuid.uuid4().hex
    t0 = time.monotonic()
    try:
        file_size = ""
        is_video = False
        if audio_path:
            p = Path(audio_path)
            if p.exists():
                file_size = p.stat().st_size
                is_video = is_video_file(p)
        _dbg_report(
            hypothesis_id="A",
            msg="offline_transcribe_enter",
            location="pat_funasr_webui/gradio_app.py:safe_transcribe_with_exports",
            trace_id=trace_id,
            data={
                "model": model,
                "preview_format": preview_format,
                "file": str(Path(audio_path).name) if audio_path else "",
                "size": file_size,
                "is_video": bool(is_video),
            },
        )
    except Exception:
        pass
    # #endregion
    try:
        result = transcribe_audio_with_exports(
            base_url=base_url,
            audio_path=audio_path,
            model=model,
            response_format=preview_format,
            timeout=timeout,
            language=language,
            hotword=hotword,
            vad_preset=vad_preset,
            merge_vad=merge_vad,
            use_itn=use_itn,
            merge_length_s=merge_length_s,
            max_line_width=max_line_width,
            batch_size_s=batch_size_s,
            batch_size_threshold_s=batch_size_threshold_s,
            vad_max_single_segment_time=vad_max_single_segment_time,
            punc_mode=punc_mode,
            device=device,
            hub=hub,
            disable_update=disable_update,
            ncpu=ncpu,
            log_level=log_level,
            disable_pbar=disable_pbar,
        )
        # #region debug-point A:offline-exit
        try:
            preview_text, raw_json = result[0], result[1]
            _dbg_report(
                hypothesis_id="A",
                msg="offline_transcribe_exit",
                location="pat_funasr_webui/gradio_app.py:safe_transcribe_with_exports",
                trace_id=trace_id,
                data={
                    "elapsed_s": round(time.monotonic() - t0, 3),
                    "preview_len": len(preview_text or ""),
                    "raw_json_len": len(raw_json or ""),
                    "download_zip": result[-1] or "",
                },
            )
        except Exception:
            pass
        # #endregion
        return result
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        # #region debug-point A:offline-http-error
        try:
            _dbg_report(
                hypothesis_id="A",
                msg="offline_transcribe_http_error",
                location="pat_funasr_webui/gradio_app.py:safe_transcribe_with_exports",
                trace_id=trace_id,
                data={
                    "elapsed_s": round(time.monotonic() - t0, 3),
                    "code": getattr(error, "code", ""),
                    "url": getattr(error, "url", ""),
                    "detail_len": len(detail or ""),
                },
            )
        except Exception:
            pass
        # #endregion
        return "", f"HTTP {error.code} from {error.url}: {detail}", None, None, None, None, None, None
    except Exception as error:
        # #region debug-point A:offline-exception
        try:
            _dbg_report(
                hypothesis_id="A",
                msg="offline_transcribe_exception",
                location="pat_funasr_webui/gradio_app.py:safe_transcribe_with_exports",
                trace_id=trace_id,
                data={
                    "elapsed_s": round(time.monotonic() - t0, 3),
                    "error": str(error),
                },
            )
        except Exception:
            pass
        # #endregion
        return "", f"Transcription failed: {error}", None, None, None, None, None, None


def recognize_emotion(
    base_url: str,
    audio_path: str | None,
    model: str,
    granularity: str,
    timeout: float,
) -> tuple[str, str]:
    """调用后端情感识别接口。"""
    if not audio_path:
        return "", "上传音频/视频文件后再点击情感识别。"

    base_url = base_url.rstrip("/")
    path = Path(audio_path)
    if is_video_file(path):
        extracted = extract_audio_from_video(str(path))
        if extracted:
            path = Path(extracted)
        else:
            return "", "不支持的视频格式，仅支持音频文件(wav/mp3/m4a/flac/ogg)"

    fields = {
        "model": str(model),
        "granularity": str(granularity),
    }
    body, boundary = multipart_body(path, fields)
    request = urllib.request.Request(
        f"{base_url}/v1/funasr/emotion",
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )
    with open_url(request, timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))

    top_emotion = payload.get("top_emotion", "")
    top_score = payload.get("top_score", 0.0)
    emotions = payload.get("emotions", [])
    ranking = " / ".join(f"{item.get('label', '')}:{float(item.get('score', 0.0)):.3f}" for item in emotions[:5])
    summary = f"主情感：{top_emotion} ({float(top_score):.3f})"
    if ranking:
        summary = f"{summary}\n排序：{ranking}"
    return summary, json.dumps(payload, ensure_ascii=False, indent=2)


def safe_recognize_emotion(
    base_url: str,
    audio_path: str | None,
    model: str,
    granularity: str,
    timeout: float,
) -> tuple[str, str]:
    """安全调用情感识别接口。"""
    try:
        return recognize_emotion(
            base_url=base_url,
            audio_path=audio_path,
            model=model,
            granularity=granularity,
            timeout=timeout,
        )
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        message = f"HTTP {error.code} from {error.url}: {detail}"
        return "", message
    except Exception as error:
        return "", f"Emotion recognition failed: {error}"


def recognize_diarization(
    base_url: str,
    audio_path: str | None,
    model: str,
    spk_model: str,
    spk_mode: str,
    preset_spk_num: int | None,
    timeout: float,
) -> tuple[str, str]:
    """调用后端说话人分离接口。"""
    payload = request_diarization_payload(
        base_url=base_url,
        audio_path=audio_path,
        model=model,
        spk_model=spk_model,
        spk_mode=spk_mode,
        preset_spk_num=preset_spk_num,
        timeout=timeout,
    )
    return summarize_diarization_payload(payload), json.dumps(payload, ensure_ascii=False, indent=2)


def request_diarization_payload(
    base_url: str,
    audio_path: str | None,
    model: str,
    spk_model: str,
    spk_mode: str,
    preset_spk_num: int | None,
    timeout: float,
) -> dict:
    """调用后端说话人分离接口并返回原始 JSON。"""
    if not audio_path:
        raise ValueError("上传音频/视频文件后再点击说话人分离。")

    base_url = base_url.rstrip("/")
    path = Path(audio_path)
    if is_video_file(path):
        extracted = extract_audio_from_video(str(path))
        if extracted:
            path = Path(extracted)
        else:
            raise ValueError("不支持的视频格式，仅支持音频文件(wav/mp3/m4a/flac/ogg)")

    fields = {
        "model": str(model),
        "spk_model": str(spk_model),
        "spk_mode": str(spk_mode),
    }
    if preset_spk_num is not None and int(preset_spk_num) > 0:
        fields["preset_spk_num"] = str(int(preset_spk_num))
    body, boundary = multipart_body(path, fields)
    request = urllib.request.Request(
        f"{base_url}/v1/funasr/diarization",
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )
    with open_url(request, timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def summarize_diarization_payload(payload: dict) -> str:
    """把 diarization JSON 摘要化，便于页面快速预览。"""
    speakers = payload.get("speakers", [])
    segments = payload.get("segments", [])
    summary_lines = [
        f"说话人数：{len(speakers)}",
        f"说话人：{', '.join(str(item) for item in speakers) if speakers else '无'}",
        f"分段数：{len(segments)}",
    ]
    for seg in segments[:6]:
        summary_lines.append(
            f"[spk={seg.get('speaker', '-')}] {seg.get('start', 0)}-{seg.get('end', 0)} {seg.get('text', '')}"
        )
    return "\n".join(summary_lines)


def build_diarization_export_files(payload: dict) -> dict[str, str]:
    """基于 diarization JSON 生成多格式导出文件，供前端直接下载。"""
    segments = payload.get("segments")
    if not isinstance(segments, list):
        segments = []
    full_text = str(payload.get("text", "") or "")
    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    verbose_payload = diarization_renderers.build_verbose_json_payload(
        full_text=full_text,
        segments=segments,
        meta={
            key: value
            for key, value in payload.items()
            if key not in {"text", "segments"}
        },
    )
    archive_bytes = diarization_renderers.render_all_zip(
        full_text=full_text,
        segments=segments,
        json_payload=verbose_payload,
    )
    exports: dict[str, str] = {}
    text_outputs = {
        "json": json_text,
        "txt": diarization_renderers.render_txt(segments),
        "srt": diarization_renderers.render_srt(segments),
        "vtt": diarization_renderers.render_vtt(segments),
        "tsv": diarization_renderers.render_tsv(segments),
    }
    for response_format, content in text_outputs.items():
        output_path = (
            Path(tempfile.gettempdir())
            / f"pat-funasr-diarization-{uuid.uuid4().hex}-{output_filename_for_format(response_format)}"
        )
        output_path.write_text(content, encoding="utf-8")
        exports[response_format] = str(output_path)
    archive_path = (
        Path(tempfile.gettempdir())
        / f"pat-funasr-diarization-{uuid.uuid4().hex}-{output_filename_for_format('all')}"
    )
    archive_path.write_bytes(archive_bytes)
    exports["all"] = str(archive_path)
    return exports


def recognize_diarization_with_exports(
    base_url: str,
    audio_path: str | None,
    model: str,
    spk_model: str,
    spk_mode: str,
    preset_spk_num: int | None,
    preview_format: str,
    timeout: float,
) -> tuple[str, str, str, str | None, str | None, str | None, str | None, str | None, str | None]:
    """调用说话人分离，并附带生成预览文本和多格式下载文件。"""
    payload = request_diarization_payload(
        base_url=base_url,
        audio_path=audio_path,
        model=model,
        spk_model=spk_model,
        spk_mode=spk_mode,
        preset_spk_num=preset_spk_num,
        timeout=timeout,
    )
    exports = build_diarization_export_files(payload)
    return (
        summarize_diarization_payload(payload),
        render_diarization_preview(payload, preview_format),
        json.dumps(payload, ensure_ascii=False, indent=2),
        exports.get("json"),
        exports.get("txt"),
        exports.get("srt"),
        exports.get("vtt"),
        exports.get("tsv"),
        exports.get("all"),
    )


def safe_recognize_diarization(
    base_url: str,
    audio_path: str | None,
    model: str,
    spk_model: str,
    spk_mode: str,
    preset_spk_num: int | None,
    timeout: float,
) -> tuple[str, str]:
    """安全调用说话人分离接口。"""
    try:
        return recognize_diarization(
            base_url=base_url,
            audio_path=audio_path,
            model=model,
            spk_model=spk_model,
            spk_mode=spk_mode,
            preset_spk_num=preset_spk_num,
            timeout=timeout,
        )
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        message = f"HTTP {error.code} from {error.url}: {detail}"
        return "", message
    except Exception as error:
        return "", f"Diarization failed: {error}"


def safe_recognize_diarization_with_exports(
    base_url: str,
    audio_path: str | None,
    model: str,
    spk_model: str,
    spk_mode: str,
    preset_spk_num: int | None,
    preview_format: str,
    timeout: float,
) -> tuple[str, str, str, str | None, str | None, str | None, str | None, str | None, str | None]:
    """安全调用说话人分离，并返回摘要、原始 JSON 与下载文件路径。"""
    try:
        return recognize_diarization_with_exports(
            base_url=base_url,
            audio_path=audio_path,
            model=model,
            spk_model=spk_model,
            spk_mode=spk_mode,
            preset_spk_num=preset_spk_num,
            preview_format=preview_format,
            timeout=timeout,
        )
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        message = f"HTTP {error.code} from {error.url}: {detail}"
        return "", message, message, None, None, None, None, None, None
    except Exception as error:
        message = f"Diarization failed: {error}"
        return "", message, message, None, None, None, None, None, None


def safe_translate_with_exports(
    base_url: str,
    text: str | None,
    file_path: str | None,
    source_lang: str,
    target_lang: str,
    model: str,
    timeout: float,
    num_beams: int = 5,
    max_length: int = 512,
    auto_zh_punc: bool = False,
):
    """执行翻译，处理完毕后返回翻译结果文本以及生成的临时结果文件路径（保存在 gr.State 里）。"""
    import gradio as gr
    try:
        from translation_utils import translate_file, translate_text_preserving_paragraphs, convert_to_chinese_punctuation

        # 1. 字幕或文本文件翻译
        if file_path:
            p = Path(file_path)
            file_ext = p.suffix
            logger.info(f"Translating file: {file_path} from {source_lang} to {target_lang}")

            out_path = translate_file(
                base_url=base_url,
                file_path=file_path,
                file_ext=file_ext,
                source_lang=source_lang,
                target_lang=target_lang,
                model=model,
                timeout=timeout,
                num_beams=num_beams,
                max_length=max_length,
            )

            # 若勾选自动替换为中文全角标点，对翻译好的结果文件内容执行转换
            if auto_zh_punc:
                out_p = Path(out_path)
                translated_content = out_p.read_text(encoding="utf-8", errors="replace")
                converted_content = convert_to_chinese_punctuation(translated_content)
                out_p.write_text(converted_content, encoding="utf-8-sig")

            out_p = Path(out_path)
            preview_text = out_p.read_text(encoding="utf-8", errors="replace")
            if len(preview_text) > 8000:
                preview_text = preview_text[:8000] + "\n\n...(内容较长，已截断预览，请点击下方[生成并导出文件]按钮进行完整下载)..."

            return preview_text, out_path, gr.update(value=None, visible=False)

        # 2. 长文本输入框翻译
        if text and text.strip():
            logger.info(f"Translating raw text from {source_lang} to {target_lang}")
            translated_result = translate_text_preserving_paragraphs(
                base_url=base_url,
                text=text,
                source_lang=source_lang,
                target_lang=target_lang,
                model=model,
                timeout=timeout,
                num_beams=num_beams,
                max_length=max_length,
            )

            # 若勾选自动替换为中文全角标点，执行文本转换
            if auto_zh_punc:
                translated_result = convert_to_chinese_punctuation(translated_result)

            return translated_result, None, gr.update(value=None, visible=False)

        raise ValueError("请输入需要翻译的文本内容，或者上传待翻译的文本/字幕文件")
    except Exception as e:
        logger.error(f"翻译执行失败: {e}")
        return f"翻译失败，错误信息：\n{e}", None, gr.update(value=None, visible=False)


def safe_export_translation_file(
    translated_text: str | None,
    result_file_path: str | None,
    original_file_path: str | None,
    source_lang: str = "",

    target_lang: str = "",

):
    """点击“生成并导出文件”时触发。若有文件翻译路径则直接返回，若是文本框翻译则将最新文本保存为临时文件。"""
    import gradio as gr
    try:
        # 1. 针对文件翻译场景，直接返回已处理完毕的翻译文件路径
        if result_file_path and Path(result_file_path).exists():
            return gr.update(value=result_file_path, visible=True)

        # 2. 针对文本框输入翻译，将最新的翻译结果渲染为临时 txt 文件返回
        if translated_text and translated_text.strip():
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            src_short = source_lang.split("_")[0] if "_" in source_lang else source_lang
            tgt_short = target_lang.split("_")[0] if "_" in target_lang else target_lang

            temp_dir = Path(tempfile.mkdtemp(prefix="pat-funasr-trans-"))
            if original_file_path:
                orig_p = Path(original_file_path)
                out_name = f"{orig_p.stem}_{src_short}_{tgt_short}_{timestamp}.txt"
            else:
                out_name = f"translated_{src_short}_{tgt_short}_{timestamp}.txt"

            out_file = temp_dir / out_name
            out_file.write_text(translated_text, encoding="utf-8-sig")
            return gr.update(value=str(out_file), visible=True)

        raise ValueError("无可导出的翻译结果。请先输入文本或上传文件，并点击“开始翻译”")
    except Exception as e:
        logger.error(f"导出翻译文件失败: {e}")
        raise gr.Error(f"导出文件失败: {e}")


def safe_check(base_url: str, timeout: float) -> str:
    try:
        return check_service(base_url, timeout)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        return f"HTTP {error.code} from {error.url}: {detail}"
    except Exception as error:
        return f"Service check failed: {error}"


def safe_check_with_capabilities(base_url: str, timeout: float, capability_filter: str) -> tuple[str, str, str, str]:
    """安全检查服务，并返回能力看板。"""
    try:
        return check_service_and_capabilities(base_url, timeout, capability_filter)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        message = f"HTTP {error.code} from {error.url}: {detail}"
        return (
            message,
            "### 运行概览\n\n加载失败，无法生成运行概览。",
            "模型能力看板加载失败。",
            "### 使用建议\n\n加载失败，无法生成建议入口。",
        )
    except Exception as error:
        message = f"Service check failed: {error}"
        return (
            message,
            "### 运行概览\n\n加载失败，无法生成运行概览。",
            "模型能力看板加载失败。",
            "### 使用建议\n\n加载失败，无法生成建议入口。",
        )


def safe_render_capabilities(base_url: str, timeout: float, capability_filter: str) -> tuple[str, str, str]:
    """仅刷新模型能力看板与使用建议，供筛选条件切换时使用。"""
    try:
        models = request_json(f"{base_url.rstrip('/')}/v1/models", timeout)
        return (
            render_service_overview_markdown(
                models,
                base_url=base_url.rstrip("/"),
                capability_filter=capability_filter,
            ),
            render_model_capability_markdown(models, capability_filter=capability_filter),
            render_capability_target_markdown(models, capability_filter=capability_filter),
        )
    except Exception as error:
        return (
            f"### 运行概览\n\n加载失败：{error}",
            f"### 模型能力看板\n\n加载失败：{error}",
            "### 使用建议\n\n加载失败，无法生成建议入口。",
        )


def auto_refresh_service_dashboard_guard(
    enabled: bool,
    tab_active: bool,
    base_url: str,
    timeout: float,
    capability_filter: str,
    max_lines: int,
    max_bytes_kb: int,
    max_section_chars: int,
):
    """自动刷新服务与调试页；关闭时返回空更新，避免持续触发。"""
    try:
        import gradio as gr
    except Exception:
        return (None, None, None, None, None, None)
    if not enabled or not tab_active:
        return (
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
        )
    try:
        status_text, raw_json, overview_markdown, capability_markdown, target_markdown = build_service_dashboard_snapshot(
            base_url,
            timeout,
            capability_filter,
        )
        runtime_logs = read_runtime_logs_ui(max_lines, max_bytes_kb, max_section_chars)
        return (
            status_text,
            raw_json,
            overview_markdown,
            capability_markdown,
            target_markdown,
            runtime_logs,
        )
    except Exception as error:
        message = f"Service auto refresh failed: {error}"
        return (
            message,
            message,
            f"### 运行概览\n\n自动刷新失败：{error}",
            f"### 模型能力看板\n\n自动刷新失败：{error}",
            "### 使用建议\n\n自动刷新失败，无法生成建议入口。",
            read_runtime_logs_ui(max_lines, max_bytes_kb, max_section_chars),
        )


def set_service_tab_auto_refresh_active(active: bool) -> bool:
    """记录当前是否停留在服务与调试页，避免后台 Tab 也持续自动刷新。"""
    return bool(active)


def auto_refresh_runtime_panels_guard(
    enabled: bool,
    tab_active: bool,
    base_url: str,
    timeout: float,
):
    """仅在服务页激活时刷新资源与任务队列。"""
    try:
        import gradio as gr
    except Exception:
        return None, None
    if not enabled or not tab_active:
        return gr.update(), gr.update()
    return safe_build_runtime_panels(base_url, timeout)


def activate_and_refresh_service_tab(
    base_url: str,
    timeout: float,
    capability_filter: str,
    max_lines: int,
    max_bytes_kb: int,
    max_section_chars: int,
):
    """进入服务页时立即刷新一次，避免必须等待下一次 Timer tick。"""
    _choices, status_text, _models_payload = fetch_model_choices(base_url.rstrip("/"), timeout)
    raw_json, overview_markdown, capability_markdown, target_markdown = safe_check_with_capabilities(
        base_url,
        timeout,
        capability_filter,
    )
    try:
        runtime_logs = read_runtime_logs_ui(max_lines, max_bytes_kb, max_section_chars)
    except Exception as error:
        runtime_logs = f"运行日志读取失败：{error}"
    return (
        True,
        status_text,
        raw_json,
        overview_markdown,
        capability_markdown,
        target_markdown,
        runtime_logs,
    )


def build_result_file_from_payload(response_format: str, raw_content: str) -> str:
    """为 JSON 类响应补一个可下载文件。"""
    output_path = Path(tempfile.gettempdir()) / f"pat-funasr-{uuid.uuid4().hex}-{output_filename_for_format(response_format)}"
    output_path.write_text(raw_content, encoding="utf-8")
    return str(output_path)


def build_batch_archive(results: list[dict[str, str]]) -> str | None:
    """把批量结果打成 zip，便于一次下载。"""
    if not results:
        return None

    archive_path = Path(tempfile.gettempdir()) / f"pat-funasr-batch-{uuid.uuid4().hex}.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for index, item in enumerate(results, start=1):
            file_name = item.get("file_name", f"item-{index}")
            stem = Path(file_name).stem or f"item-{index}"
            result_path = item.get("result_path")
            if item.get("ok") and result_path and Path(result_path).exists():
                target_name = f"{index:02d}-{stem}-{Path(result_path).name}"
                zf.write(result_path, arcname=target_name)
                continue
            error_text = item.get("message", "未知错误")
            zf.writestr(f"{index:02d}-{stem}-error.txt", error_text)
    return str(archive_path)


def batch_transcribe(
    batch_files,
    base_url: str,
    model: str,
    response_format: str,
    timeout: float,
    language: str | None,
    hotword: str | None,
    vad_preset: str | None,
    merge_vad: str | bool | None,
    use_itn: str | bool | None,
    merge_length_s: int | None,
    max_line_width: int | None,
    batch_size_s: int | None,
    batch_size_threshold_s: int | None,
    vad_max_single_segment_time: int | None,
    punc_mode: str | None,
    device: str | None,
    hub: str | None,
    disable_update: str | bool | None,
    ncpu: int | None,
    log_level: str | None,
    disable_pbar: str | bool | None,
):
    """顺序执行批量转写，并流式返回汇总与打包结果。"""
    import gradio as gr

    paths = normalize_uploaded_paths(batch_files)
    hidden_batch_download = gr.update(value=None, visible=False)
    if not paths:
        yield "请先上传至少一个批量文件。", hidden_batch_download, []
        return

    results = initialize_batch_results(paths)
    yield summarize_batch_results(results), hidden_batch_download, []

    for index, file_path in enumerate(paths):
        results[index]["status"] = "running"
        results[index]["message"] = ""
        if index % BATCH_RUNNING_STATUS_UPDATE_EVERY_ITEMS == 0:
            yield summarize_batch_results(results), hidden_batch_download, [
                item["source_path"] for item in results if item.get("status") == "error"
            ]

        transcript, raw_content, download_path = safe_transcribe(
            base_url=base_url,
            audio_path=file_path,
            model=model,
            response_format=response_format,
            timeout=timeout,
            language=language,
            hotword=hotword,
            vad_preset=vad_preset,
            merge_vad=merge_vad,
            use_itn=use_itn,
            merge_length_s=merge_length_s,
            max_line_width=max_line_width,
            batch_size_s=batch_size_s,
            batch_size_threshold_s=batch_size_threshold_s,
            vad_max_single_segment_time=vad_max_single_segment_time,
            punc_mode=punc_mode,
            device=device,
            hub=hub,
            disable_update=disable_update,
            ncpu=ncpu,
            log_level=log_level,
            disable_pbar=disable_pbar,
        )
        ok = bool(download_path) or (
            not raw_content.startswith("HTTP ") and not raw_content.startswith("Transcription failed:")
        )
        if ok:
            result_path = download_path or build_result_file_from_payload(response_format, raw_content)
            results[index]["status"] = "success"
            results[index]["ok"] = True
            results[index]["message"] = truncate_tail_text(transcript or "ok", BATCH_ITEM_MESSAGE_MAX_CHARS)
            results[index]["result_path"] = result_path
        else:
            results[index]["status"] = "error"
            results[index]["ok"] = False
            results[index]["message"] = truncate_tail_text(
                raw_content or transcript or "未知错误",
                BATCH_ITEM_MESSAGE_MAX_CHARS,
            )
            results[index]["result_path"] = ""

        yield summarize_batch_results(results), hidden_batch_download, [
            item["source_path"] for item in results if item.get("status") == "error"
        ]

    summary = summarize_batch_results(results)
    archive_path = build_batch_archive(results)
    failed_paths = [item["source_path"] for item in results if item.get("status") == "error"]
    yield summary, gr.update(value=archive_path, visible=bool(archive_path)), failed_paths


def retry_failed_batch(
    failed_paths,
    base_url: str,
    model: str,
    response_format: str,
    timeout: float,
    language: str | None,
    hotword: str | None,
    vad_preset: str | None,
    merge_vad: str | bool | None,
    use_itn: str | bool | None,
    merge_length_s: int | None,
    max_line_width: int | None,
    batch_size_s: int | None,
    batch_size_threshold_s: int | None,
    vad_max_single_segment_time: int | None,
    punc_mode: str | None,
    device: str | None,
    hub: str | None,
    disable_update: str | bool | None,
    ncpu: int | None,
    log_level: str | None,
    disable_pbar: str | bool | None,
):
    """仅重试上次失败的文件。"""
    return batch_transcribe(
        batch_files=failed_paths,
        base_url=base_url,
        model=model,
        response_format=response_format,
        timeout=timeout,
        language=language,
        hotword=hotword,
        vad_preset=vad_preset,
        merge_vad=merge_vad,
        use_itn=use_itn,
        merge_length_s=merge_length_s,
        max_line_width=max_line_width,
        batch_size_s=batch_size_s,
        batch_size_threshold_s=batch_size_threshold_s,
        vad_max_single_segment_time=vad_max_single_segment_time,
        punc_mode=punc_mode,
        device=device,
        hub=hub,
        disable_update=disable_update,
        ncpu=ncpu,
        log_level=log_level,
        disable_pbar=disable_pbar,
    )


def get_model_source_hint_html(status_text: str) -> str:
    """生成模型来源的状态指示 HTML 字符串（带主题适配）。"""
    try:
        if status_text and "后端实时" in str(status_text):
            return "<div class='pat-model-source-hint' style='margin-top: 4px; font-size: 13px; color: #10B981; display: flex; align-items: center; gap: 4px;'><span style='font-size: 8px;'>●</span> 当前为后端实时模型列表</div>"
    except Exception:
        pass
    return ""


def fetch_model_choices(base_url: str, timeout: float) -> tuple[list[tuple[str, str]], str, dict]:
    """从后端读取模型列表，失败时返回静态兜底选项。"""
    fallback_choices = build_known_model_choices()
    fallback_payload = {"data": [{"id": value, "ready": False} for _, value in fallback_choices]}
    try:
        payload = request_json(f"{base_url.rstrip('/')}/v1/models", timeout)
        choices = parse_model_choices(payload)
        if choices:
            return (
                choices,
                f"当前为后端实时模型列表\n{summarize_model_status(payload)}",
                payload,
            )
        return (
            fallback_choices,
            "后端返回了空模型列表，已回退静态模型清单",
            fallback_payload,
        )
    except Exception as error:
        return (
            fallback_choices,
            f"模型列表加载失败，已回退静态模型清单：{error}",
            fallback_payload,
        )


def explain_response_format(response_format: str) -> str:
    """根据当前输出格式给出简短说明。"""
    descriptions = {
        "json": "返回简洁 JSON，仅含 text。",
        "verbose_json": "返回完整 JSON，包含 segments 与 meta。",
        "txt": "返回纯文本，可直接下载。",
        "srt": "返回 SRT 字幕，可直接下载。",
        "vtt": "返回 VTT 字幕，可直接下载。",
        "tsv": "返回 TSV 时间戳表，可直接下载。",
        "all": "返回 ZIP 压缩包，包含 txt/json/srt/vtt/tsv。",
    }
    return descriptions.get(response_format, "")


def render_transcription_preview(payload: dict, preview_format: str) -> str:
    """把离线识别结果渲染为页面预览文本。"""
    segments = payload.get("segments")
    if not isinstance(segments, list):
        segments = []
    full_text = str(payload.get("text", "") or "")
    preview_format = str(preview_format or DEFAULT_PREVIEW_FORMAT)
    if preview_format == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2)
    if preview_format == "txt":
        return diarization_renderers.render_txt(segments)
    if preview_format == "srt":
        return diarization_renderers.render_srt(segments)
    if preview_format == "vtt":
        return diarization_renderers.render_vtt(segments)
    if preview_format == "tsv":
        return diarization_renderers.render_tsv(segments)
    return full_text


def render_diarization_preview(payload: dict, preview_format: str) -> str:
    """把说话人分离结果渲染为页面预览文本。"""
    segments = payload.get("segments")
    if not isinstance(segments, list):
        segments = []
    preview_format = str(preview_format or DEFAULT_PREVIEW_FORMAT)
    if preview_format == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2)
    if preview_format == "txt":
        return diarization_renderers.render_txt(segments)
    if preview_format == "srt":
        return diarization_renderers.render_srt(segments)
    if preview_format == "vtt":
        return diarization_renderers.render_vtt(segments)
    if preview_format == "tsv":
        return diarization_renderers.render_tsv(segments)
    return summarize_diarization_payload(payload)


def update_transcription_preview(preview_format: str, payload_json: str) -> str:
    """根据当前预览格式切换离线识别结果展示。"""
    preview_text = read_preview_text_from_state(preview_format, payload_json)
    if preview_text is not None:
        return preview_text
    try:
        payload = json.loads(payload_json or "{}")
    except Exception:
        return payload_json or ""
    return render_transcription_preview(payload, preview_format)


def update_diarization_preview(preview_format: str, payload_json: str) -> str:
    """根据当前预览格式切换说话人分离结果展示。"""
    try:
        payload = json.loads(payload_json or "{}")
    except Exception:
        return payload_json or ""
    return render_diarization_preview(payload, preview_format)


def build_transcription_export_files(payload: dict) -> dict[str, str]:
    """基于离线识别 JSON 生成多格式导出文件。"""
    segments = payload.get("segments")
    if not isinstance(segments, list):
        segments = []
    full_text = str(payload.get("text", "") or "")
    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    archive_bytes = diarization_renderers.render_all_zip(
        full_text=full_text,
        segments=segments,
        json_payload=payload,
    )
    exports: dict[str, str] = {}
    text_outputs = {
        "json": json_text,
        "txt": diarization_renderers.render_txt(segments),
        "srt": diarization_renderers.render_srt(segments),
        "vtt": diarization_renderers.render_vtt(segments),
        "tsv": diarization_renderers.render_tsv(segments),
    }
    for response_format, content in text_outputs.items():
        output_path = (
            Path(tempfile.gettempdir())
            / f"pat-funasr-transcription-{uuid.uuid4().hex}-{output_filename_for_format(response_format)}"
        )
        output_path.write_text(content, encoding="utf-8")
        exports[response_format] = str(output_path)
    archive_path = (
        Path(tempfile.gettempdir())
        / f"pat-funasr-transcription-{uuid.uuid4().hex}-{output_filename_for_format('all')}"
    )
    archive_path.write_bytes(archive_bytes)
    exports["all"] = str(archive_path)
    return exports


def build_fine_export_files(payload: dict) -> dict[str, str]:
    """
    精细转录专属多格式导出：
    - TXT : 完整包含 转写+纪要+思维导图
    - JSON: payload 原样（含 summary/mindmap/refined_text）
    - ZIP : 额外追加 transcript_refined.txt / summary.md / mindmap.json
    SRT/VTT/TSV 与离线识别保持一致（按 segments）
    """
    import json as _json_mod

    segments = payload.get("segments") or []
    full_text = str(payload.get("text", "") or "")
    refined_text = str(payload.get("refined_text", "") or "")
    summary = payload.get("summary") or {}
    mindmap = payload.get("mindmap") or {}
    scene_name = str(payload.get("scene_name", "") or "")
    elapsed = float(payload.get("elapsed") or 0)

    # JSON：payload 全量（包含 summary/mindmap/refined_text）
    json_text = _json_mod.dumps(payload, ensure_ascii=False, indent=2)

    # TXT：使用 export_result(txt) 组合转写+纪要+思维导图
    txt_export = export_result(payload, format="txt")

    archive_bytes = diarization_renderers.render_fine_all_zip(
        full_text=full_text,
        refined_text=refined_text,
        segments=segments,
        json_payload=payload,
        summary=summary,
        mindmap=mindmap,
        scene_name=scene_name,
        elapsed=elapsed,
    )
    exports: dict[str, str] = {}
    text_outputs = {
        "json": json_text,
        "txt": txt_export,
        "srt": diarization_renderers.render_srt(segments),
        "vtt": diarization_renderers.render_vtt(segments),
        "tsv": diarization_renderers.render_tsv(segments),
    }
    for response_format, content in text_outputs.items():
        output_path = (
            Path(tempfile.gettempdir())
            / f"pat-funasr-fine-{uuid.uuid4().hex}-{output_filename_for_format(response_format)}"
        )
        output_path.write_text(content, encoding="utf-8")
        exports[response_format] = str(output_path)
    archive_path = (
        Path(tempfile.gettempdir())
        / f"pat-funasr-fine-{uuid.uuid4().hex}-{output_filename_for_format('all')}"
    )
    archive_path.write_bytes(archive_bytes)
    exports["all"] = str(archive_path)
    return exports


def format_streaming_preview_text(full_text: str, final_flag: bool) -> str:
    """把 streaming 全量文本整理为预览文本，合并过短句并按自然边界换行。"""
    _ = final_flag
    return truncate_tail_text(format_streaming_text_for_display(full_text), STREAMING_PREVIEW_MAX_CHARS)


def update_media_preview(file_path: str | None):
    """根据已选择文件更新视频预览与提示。"""
    try:
        import gradio as gr
    except ImportError as error:
        raise SystemExit("Install Gradio first: pip install gradio") from error

    if not file_path:
        return (
            gr.update(value=None, visible=False),
            gr.update(value=None, visible=False),
            "支持音频与视频文件。视频和音频都会显示可播放预览。",
        )
    if is_video_file(file_path):
        return (
            gr.update(value=file_path, visible=True),
            gr.update(value=None, visible=False),
            f"已加载视频：{Path(file_path).name}",
        )
    return (
        gr.update(value=None, visible=False),
        gr.update(value=file_path, visible=True),
        f"已加载音频：{Path(file_path).name}",
    )


def build_reserved_feature_tab(
    gr,
    *,
    title: str,
    description: str,
    planned_inputs: list[str],
    planned_outputs: list[str],
):
    """构建后续功能的预留页骨架，便于后面直接挂真实能力。"""
    gr.Markdown(f"### {title}")
    gr.Markdown(description, elem_classes=["pat-placeholder-box"])
    with gr.Row():
        with gr.Column():
            gr.Markdown("**计划输入参数**")
            for item in planned_inputs:
                gr.Textbox(label=item, placeholder="预留中", interactive=False)
        with gr.Column():
            gr.Markdown("**计划输出结果**")
            for item in planned_outputs:
                gr.Textbox(label=item, placeholder="预留中", interactive=False)
    with gr.Row():
        gr.Button("预留执行入口", interactive=False, variant="primary")
        gr.Button("预留下载入口", interactive=False, variant="secondary")


def refresh_model_dropdown(base_url: str, timeout: float):
    """刷新模型下拉框，并同步返回状态文本。"""
    try:
        import gradio as gr
    except ImportError as error:
        raise SystemExit("Install Gradio first: pip install gradio") from error

    choices, status_text, _ = fetch_model_choices(base_url, timeout)
    # 离线识别只显示 ASR 转写模型（排除翻译/情感模型）
    asr_choices = filter_asr_model_choices(choices)
    streaming_choices = ensure_dropdown_choices(
        filter_streaming_model_choices(choices),
        fallback=DEFAULT_STREAMING_MODEL,
    )
    emotion_choices = ensure_dropdown_choices(
        filter_emotion_model_choices(choices),
        fallback=DEFAULT_EMOTION_MODEL,
    )
    diarization_choices = ensure_dropdown_choices(
        filter_diarization_model_choices(choices),
        fallback=DEFAULT_DIARIZATION_MODEL,
    )
    hint_html = get_model_source_hint_html(status_text)
    return (
        gr.update(choices=asr_choices, value=choose_default_model(asr_choices) or DEFAULT_MODEL),
        gr.update(
            choices=streaming_choices,
            value=choose_default_streaming_model(streaming_choices) or DEFAULT_STREAMING_MODEL,
        ),
        gr.update(
            choices=emotion_choices,
            value=choose_default_emotion_model(emotion_choices) or DEFAULT_EMOTION_MODEL,
        ),
        gr.update(
            choices=diarization_choices,
            value=choose_default_diarization_model(diarization_choices) or DEFAULT_DIARIZATION_MODEL,
        ),
        status_text,
        hint_html,
        hint_html,
        hint_html,
        hint_html,
        hint_html,
    )


def initialize_service_dashboard(base_url: str, timeout: float, capability_filter: str):
    """页面加载时自动初始化服务页，减少手动点击刷新。"""
    try:
        import gradio as gr
    except ImportError as error:
        raise SystemExit("Install Gradio first: pip install gradio") from error

    normalized_base_url = base_url.rstrip("/")
    choices, status_text, models_payload = fetch_model_choices(normalized_base_url, timeout)
    # 离线识别只显示 ASR 转写模型（排除翻译/情感模型）
    asr_choices = filter_asr_model_choices(choices)
    streaming_choices = ensure_dropdown_choices(
        filter_streaming_model_choices(choices),
        fallback=DEFAULT_STREAMING_MODEL,
    )
    emotion_choices = ensure_dropdown_choices(
        filter_emotion_model_choices(choices),
        fallback=DEFAULT_EMOTION_MODEL,
    )
    diarization_choices = ensure_dropdown_choices(
        filter_diarization_model_choices(choices),
        fallback=DEFAULT_DIARIZATION_MODEL,
    )
    try:
        health = request_json(f"{normalized_base_url}/health", timeout)
        raw_json = json.dumps({"health": health, "models": models_payload}, ensure_ascii=False, indent=2)
        overview_markdown = render_service_overview_markdown(
            models_payload,
            base_url=normalized_base_url,
            capability_filter=capability_filter,
        )
        capability_markdown = render_model_capability_markdown(models_payload, capability_filter=capability_filter)
        target_markdown = render_capability_target_markdown(models_payload, capability_filter=capability_filter)
    except Exception as error:
        raw_json = f"Service check failed: {error}"
        overview_markdown = f"### 运行概览\n\n加载失败：{error}"
        capability_markdown = f"### 模型能力看板\n\n加载失败：{error}"
        target_markdown = "### 使用建议\n\n加载失败，无法生成建议入口。"

    return (
        gr.update(choices=asr_choices, value=choose_default_model(asr_choices) or DEFAULT_MODEL),
        gr.update(
            choices=streaming_choices,
            value=choose_default_streaming_model(streaming_choices) or DEFAULT_STREAMING_MODEL,
        ),
        gr.update(
            choices=emotion_choices,
            value=choose_default_emotion_model(emotion_choices) or DEFAULT_EMOTION_MODEL,
        ),
        gr.update(
            choices=diarization_choices,
            value=choose_default_diarization_model(diarization_choices) or DEFAULT_DIARIZATION_MODEL,
        ),
        status_text,
        raw_json,
        overview_markdown,
        capability_markdown,
        target_markdown,
        read_runtime_logs(max_lines=120, max_bytes=256 * 1024, max_section_chars=8000),
    )


def update_emotion_granularity_options(model: str):
    """按情感模型约束 granularity 选项，避免无效请求。"""
    try:
        import gradio as gr
    except ImportError as error:
        raise SystemExit("Install Gradio first: pip install gradio") from error
    if model == "sensevoice":
        return gr.update(
            choices=[("utterance", "utterance")],
            value="utterance",
        )
    return gr.update(
        choices=[("utterance", "utterance"), ("frame", "frame")],
        value="utterance",
    )


def build_app(default_base_url: str, default_timeout: float):
    try:
        import gradio as gr
    except ImportError as error:
        raise SystemExit("Install Gradio first: pip install gradio") from error

    fetched_model_choices = fetch_model_choices(default_base_url, default_timeout)
    if len(fetched_model_choices) == 3:
        model_choices, model_status_text, initial_models_payload = fetched_model_choices
    else:
        model_choices, model_status_text = fetched_model_choices
        initial_models_payload = {"data": [{"id": value, "ready": False} for _, value in model_choices]}
    # 离线识别只显示 ASR 转写模型（排除翻译/情感模型）
    asr_model_choices = filter_asr_model_choices(model_choices)
    default_model_value = choose_default_model(asr_model_choices) or DEFAULT_MODEL
    streaming_model_choices = ensure_dropdown_choices(
        filter_streaming_model_choices(model_choices),
        fallback=DEFAULT_STREAMING_MODEL,
    )
    default_streaming_model_value = choose_default_streaming_model(streaming_model_choices) or DEFAULT_STREAMING_MODEL
    emotion_model_choices = ensure_dropdown_choices(
        filter_emotion_model_choices(model_choices),
        fallback=DEFAULT_EMOTION_MODEL,
    )
    default_emotion_model_value = choose_default_emotion_model(emotion_model_choices) or DEFAULT_EMOTION_MODEL
    diarization_model_choices = ensure_dropdown_choices(
        filter_diarization_model_choices(model_choices),
        fallback=DEFAULT_DIARIZATION_MODEL,
    )
    default_diarization_model_value = choose_default_diarization_model(diarization_model_choices) or DEFAULT_DIARIZATION_MODEL
    initial_overview_markdown = "### 运行概览\n\n点击“检查服务”加载。"
    initial_capability_markdown = "### 模型能力看板\n\n点击“检查服务”加载。"
    initial_target_markdown = "### 使用建议\n\n点击“检查服务”加载。"
    initial_runtime_logs = read_runtime_logs(max_lines=120, max_bytes=256 * 1024, max_section_chars=8000)

    fetched_model_choices = fetch_model_choices(default_base_url, default_timeout)

    with gr.Blocks(title="Pat-FunASR 语音识别") as demo:
        gr.Markdown("# Pat-FunASR WebUI")

        base_url = gr.Textbox(label="API 地址", value=default_base_url, visible=False)
        timeout = gr.Number(label="超时时间(秒)", value=default_timeout, precision=0, visible=False)
        service_tab_active = gr.State(False)

        # 顶层只保留四个产品域；子栏目直接嵌套，复用原组件、回调和后端接口。
        with gr.Tabs():
            with gr.Tab("转录工作台", render_children=False) as transcription_workspace_tab:
                with gr.Tabs():
                    with gr.Tab("快速转录", render_children=False) as offline_tab:
                        batch_response_format = gr.State(DEFAULT_BATCH_RESPONSE_FORMAT)
                        with gr.Row(equal_height=False):
                            with gr.Column(scale=1, min_width=320):
                                model = gr.Dropdown(
                                    label="模型",
                                    choices=asr_model_choices,
                                    value=default_model_value,
                                )
                                offline_model_source_hint = gr.HTML(
                                    value=get_model_source_hint_html(model_status_text),
                                    show_label=False
                                )
                            with gr.Column(scale=1, min_width=520):
                                with gr.Accordion("高级参数", open=False):
                                    with gr.Row():
                                        language = gr.Textbox(label="语言提示(language)", placeholder="如：zh / en / auto")
                                        hotword = gr.Textbox(label="热词(hotword)", placeholder="多个热词可用逗号分隔")
                                        vad_preset = gr.Dropdown(
                                            label="VAD 预设(vad_preset)",
                                            choices=[("自动", ""), ("default", "default"), ("anti_hallucination", "anti_hallucination")],
                                            value="",
                                        )
                                    with gr.Row():
                                        merge_vad = gr.Dropdown(
                                            label="合并 VAD 片段(merge_vad)",
                                            choices=[("自动", ""), ("启用", "true"), ("禁用", "false")],
                                            value="",
                                        )
                                        use_itn = gr.Dropdown(
                                            label="逆文本正规化(use_itn)",
                                            choices=[("自动", ""), ("启用", "true"), ("禁用", "false")],
                                            value="",
                                        )
                                        merge_length_s = gr.Number(label="合并段长度(merge_length_s)", value=15, precision=0)
                                        max_line_width = gr.Number(label="字幕单行最大长度(max_line_width)", value=40, precision=0)
                                    with gr.Row():
                                        batch_size_s = gr.Number(label="批处理时长(batch_size_s)", value=0, precision=0)
                                        batch_size_threshold_s = gr.Number(
                                            label="批处理阈值(batch_size_threshold_s)",
                                            value=0,
                                            precision=0,
                                        )
                                        vad_max_single_segment_time = gr.Number(
                                            label="VAD 单段最大时长(vad_max_single_segment_time, ms)",
                                            value=0,
                                            precision=0,
                                        )
                                        punc_mode = gr.Dropdown(
                                            label="PUNC 策略(punc_mode)",
                                            choices=[("自动", "auto"), ("关闭外置 PUNC", "disabled")],
                                            value="auto",
                                        )
                                with gr.Accordion("运行时控制", open=False):
                                    gr.Markdown("这些参数只影响当前请求，不额外拆页面。", elem_classes=["pat-compact-markdown"])
                                    with gr.Row():
                                        device = gr.Textbox(label="运行设备(device)", placeholder="如：cuda / cpu")
                                        hub = gr.Dropdown(
                                            label="模型来源(hub)",
                                            choices=[("默认", ""), ("ModelScope", "ms"), ("HuggingFace", "hf")],
                                            value=get_default_model_hub_for_ui(),
                                        )
                                        disable_update = gr.Dropdown(
                                            label="禁用更新检查(disable_update)",
                                            choices=[("默认", ""), ("启用", "true"), ("禁用", "false")],
                                            value="",
                                        )
                                    with gr.Row():
                                        ncpu = gr.Number(label="CPU 线程数(ncpu)", value=0, precision=0)
                                        log_level = gr.Dropdown(
                                            label="日志级别(log_level)",
                                            choices=[("默认", ""), ("DEBUG", "DEBUG"), ("INFO", "INFO"), ("WARNING", "WARNING"), ("ERROR", "ERROR")],
                                            value="",
                                        )
                                        disable_pbar = gr.Dropdown(
                                            label="禁用进度条(disable_pbar)",
                                            choices=[("默认", ""), ("启用", "true"), ("禁用", "false")],
                                            value="true",
                                        )
                        transcript_payload_state = gr.State("{}")
                        with gr.Row(equal_height=False):
                            with gr.Column(scale=1, min_width=420):
                                gr.Markdown("### 单文件处理", elem_classes=["pat-compact-markdown"])
                                media_file = gr.File(
                                    label="音频/视频文件",
                                    type="filepath",
                                    file_types=list(MEDIA_FILE_SUFFIXES),
                                    height=208,
                                )
                                transcribe_button = gr.Button("开始识别", variant="primary")
                                media_status = gr.Markdown(
                                    "支持音频与视频文件。视频和音频都会显示可播放预览。",
                                    elem_classes=["pat-compact-markdown"],
                                )
                                media_preview = gr.Video(
                                    label="视频预览",
                                    visible=False,
                                    height=260,
                                    elem_classes=["pat-media-preview"],
                                )
                                media_audio_preview = gr.Audio(label="音频预览", visible=False)
                                transcript_preview_format = gr.Radio(
                                    label="预览格式",
                                    choices=PREVIEW_FORMAT_CHOICES,
                                    value=DEFAULT_PREVIEW_FORMAT,
                                )
                                transcript = gr.Textbox(label="结果预览", lines=8, max_lines=20, buttons=["copy"])
                                with gr.Accordion("下载文件", open=False):
                                    with gr.Row():
                                        download_json = gr.File(label="下载 JSON", visible=True)
                                        download_txt = gr.File(label="下载 TXT", visible=True)
                                        download_srt = gr.File(label="下载 SRT", visible=True)
                                    with gr.Row():
                                        download_vtt = gr.File(label="下载 VTT", visible=True)
                                        download_tsv = gr.File(label="下载 TSV", visible=True)
                                        download_zip = gr.File(label="下载 ZIP", visible=True)
                            with gr.Column(scale=1, min_width=420):
                                gr.Markdown("### 批量文件处理", elem_classes=["pat-compact-markdown"])
                                batch_files = gr.Files(
                                    label="批量文件",
                                    file_count="multiple",
                                    type="filepath",
                                    file_types=list(MEDIA_FILE_SUFFIXES),
                                    height=176,
                                )
                                with gr.Row():
                                    batch_button = gr.Button("批量执行", variant="primary")
                                    retry_failed_button = gr.Button("重试失败项", variant="primary")
                                batch_status = gr.Textbox(label="批量结果", lines=5, max_lines=10)
                                batch_download = gr.File(label="批量下载结果", visible=False)
                        failed_batch_state = gr.State([])


                    with gr.Tab("会议精细转录", render_children=False) as fine_transcription_tab:
                        # 场景化 ASR + LLM 协同管线。
                        with gr.Row(equal_height=False):
                            # 左列：配置面板
                            with gr.Column(scale=1, min_width=340):
                                gr.Markdown("### 精细转录\n场景化 ASR + LLM 协同，产出精细文本/纪要/思维导图")

                                with gr.Row():
                                    ft_scene = gr.Dropdown(
                                        label="选择场景",
                                        choices=SCENE_CHOICES,
                                        value="general",
                                        scale=3,
                                    )
                                    ft_scene_btn = gr.Button("📋 场景详情", scale=1)

                                # 场景详情弹窗（默认隐藏）
                                ft_scene_modal = gr.Group(visible=False)
                                with ft_scene_modal:
                                    gr.Markdown("### 场景详情（可编辑 Prompt）")
                                    ft_scene_name = gr.Textbox(label="场景名称", interactive=False)
                                    ft_scene_desc_text = gr.Textbox(label="描述", interactive=False, lines=2)
                                    ft_scene_prompt = gr.Textbox(
                                        label="LLM Prompt（可手工修改）",
                                        lines=10,
                                        max_lines=20,
                                        interactive=True,
                                    )
                                    ft_scene_hotwords_display = gr.Textbox(
                                        label="预设热词（只读）",
                                        interactive=False,
                                        lines=3,
                                    )
                                    ft_scene_close_btn = gr.Button("✅ 关闭", variant="secondary")

                                # 词表管理弹窗按钮
                                ft_hotword_btn = gr.Button("📝 管理词表")
                                ft_hotwords_state = gr.State(value="")  # 存储自定义热词(弹窗内外中转)

                                # 词表弹窗（默认隐藏）
                                ft_hotword_modal = gr.Group(visible=False)
                                with ft_hotword_modal:
                                    gr.Markdown("### 词表管理\n场景预设热词（只读）+ 自定义热词（可增删改）")
                                    ft_preset_hotwords = gr.Textbox(
                                        label="场景预设热词（只读）",
                                        interactive=False,
                                        lines=4,
                                    )
                                    ft_custom_hotwords = gr.Textbox(
                                        label="自定义热词（每行一个，会追加到预设词表）",
                                        placeholder="输入自定义热词，每行一个...\n例：\n项目名\n人名\n专业术语",
                                        lines=8,
                                        max_lines=15,
                                        interactive=True,
                                    )
                                    ft_hotword_close_btn = gr.Button("✅ 关闭", variant="secondary")

                                ft_audio = gr.Audio(
                                    label="上传音频文件",
                                    type="filepath",
                                    elem_classes=["ft-audio-player"],
                                )

                                with gr.Accordion("1. 音频前处理", open=False):
                                    ft_enable_preprocess = gr.Checkbox(
                                        label="启用音频前处理",
                                        value=False,
                                    )
                                    with gr.Row():
                                        ft_noise_reduction = gr.Checkbox(label="降噪", value=True)
                                        ft_noise_strength = gr.Slider(label="降噪强度(dB)", minimum=0, maximum=48, value=8)
                                    with gr.Row():
                                        ft_sample_rate = gr.Dropdown(label="采样率", choices=[16000, 24000, 48000], value=16000)
                                        ft_loudnorm = gr.Checkbox(label="响度归一化", value=True)
                                    ft_silence_mode = gr.Radio(
                                        label="静音处理",
                                        choices=[("保留原始时间轴", "preserve_timeline"), ("裁剪静音（改变时间轴）", "trim_silence")],
                                        value="preserve_timeline",
                                    )

                                with gr.Accordion("2. VAD 与长音频分块", open=False):
                                    with gr.Row():
                                        ft_vad_enabled = gr.Checkbox(label="启用 VAD", value=True)
                                        ft_vad_preset = gr.Dropdown(
                                            label="VAD 预设",
                                            choices=["default", "anti_hallucination"],
                                            value="default",
                                        )
                                    ft_chunk_enabled = gr.Checkbox(label="启用 FFmpeg 长音频分块", value=True)
                                    with gr.Row():
                                        ft_chunk_seconds = gr.Slider(label="每块秒数", minimum=30, maximum=1800, step=30, value=240)
                                        ft_overlap_seconds = gr.Slider(label="块间重叠秒数", minimum=0, maximum=120, step=1, value=10)

                                with gr.Accordion("3. 转录模型与多模型校对", open=True):
                                    ft_model = gr.Dropdown(
                                        label="主转录模型",
                                        choices=asr_model_choices,
                                        value=default_model_value,
                                    )
                                    ft_transcription_mode = gr.Radio(
                                        label="转录模式",
                                        choices=[("单模型", "single_model"), ("多模型转录并校对", "multi_model")],
                                        value="single_model",
                                    )
                                    ft_reviewer_models = gr.Dropdown(
                                        label="校对模型（可多选，不能与主模型重复）",
                                        choices=asr_model_choices,
                                        value=[],
                                        multiselect=True,
                                    )
                                    with gr.Row():
                                        ft_primary_weight = gr.Slider(label="主模型权重", minimum=0.1, maximum=10, value=1.0)
                                        ft_reviewer_weight = gr.Slider(label="校对模型权重", minimum=0.1, maximum=10, value=1.0)
                                    with gr.Row():
                                        ft_execution = gr.Radio(label="执行方式", choices=[("串行", "serial"), ("并行", "parallel")], value="serial")
                                        ft_max_concurrency = gr.Slider(label="最大并发模型数", minimum=1, maximum=4, step=1, value=1)
                                    ft_failure_policy = gr.Dropdown(
                                        label="资源/模型失败策略",
                                        choices=[
                                            ("停止并提示", "stop_and_ask"),
                                            ("回退串行", "fallback_to_serial"),
                                            ("跳过失败的校对模型", "skip_failed_reviewer"),
                                        ],
                                        value="stop_and_ask",
                                    )
                                    with gr.Row():
                                        ft_language = gr.Textbox(label="语言", value="auto")
                                        ft_use_itn = gr.Checkbox(label="启用 ITN", value=True)
                                        ft_punc_mode = gr.Radio(label="标点", choices=[("自动", "auto"), ("关闭", "disabled")], value="auto")

                                with gr.Accordion("4. 时间戳与强制对齐", open=False):
                                    ft_timestamp_level = gr.Radio(
                                        label="时间戳粒度",
                                        choices=[("关闭", "off"), ("段级", "segment"), ("字词级", "word")],
                                        value="segment",
                                    )
                                    ft_forced_alignment = gr.Checkbox(label="启用强制对齐（需选择支持模型）", value=False)
                                    ft_aligner_model = gr.Textbox(
                                        label="对齐模型",
                                        value="Qwen/Qwen3-ForcedAligner-0.6B",
                                    )

                                with gr.Accordion("5. 说话人识别与时间轴对齐", open=True):
                                    ft_diarization_enabled = gr.Checkbox(label="启用说话人识别", value=True)
                                    ft_diarization_strategy = gr.Radio(
                                        label="策略",
                                        choices=[("独立识别后时间对齐", "separate_align")],
                                        value="separate_align",
                                    )
                                    ft_diarization_asr_model = gr.Dropdown(
                                        label="说话人辅助 ASR 模型",
                                        choices=diarization_model_choices,
                                        value=choose_default_diarization_model(diarization_model_choices) or DEFAULT_DIARIZATION_MODEL,
                                    )
                                    with gr.Row():
                                        ft_speaker_model = gr.Dropdown(label="说话人模型", choices=["cam++"], value="cam++", allow_custom_value=True)
                                        ft_spk_mode = gr.Dropdown(label="分段模式", choices=["default", "vad_segment", "punc_segment"], value="punc_segment")
                                    with gr.Row():
                                        ft_preset_speaker_count = gr.Number(label="预设说话人数（留空自动）", value=None, precision=0)
                                        ft_global_speaker_clustering = gr.Checkbox(
                                            label="全局说话人聚类（当前工作流必需）",
                                            value=True,
                                            interactive=False,
                                        )

                                with gr.Accordion("6. 多模型结果校对规则", open=False):
                                    ft_reconciliation_mode = gr.Radio(
                                        label="选择规则",
                                        choices=[("主模型优先", "primary_first"), ("用户权重共识", "weighted_consensus")],
                                        value="primary_first",
                                    )
                                    ft_disagreement_threshold = gr.Slider(label="分歧阈值", minimum=0, maximum=1, value=0.2)
                                    with gr.Row():
                                        ft_keep_alternatives = gr.Checkbox(label="保留候选文本", value=True)
                                        ft_uncertain_policy = gr.Radio(
                                            label="不确定结果",
                                            choices=[("保留主模型", "keep_primary"), ("标记人工复核", "flag_for_review")],
                                            value="flag_for_review",
                                        )

                                with gr.Accordion("7. LLM 后处理（每阶段独立选模型）", open=False):
                                    ft_enable_llm = gr.Checkbox(
                                        label="启用 LLM 二次优化",
                                        value=False,
                                    )
                                    ft_llm_select = gr.Dropdown(
                                        label="校对 LLM",
                                        choices=get_llm_choices(),
                                        value=get_default_llm_value(),
                                    )
                                    with gr.Row():
                                        ft_llm_scope = gr.Radio(
                                            label="校对范围",
                                            choices=[
                                                ("全文拼接（推荐，快 10×）", "refined"),
                                                ("逐时间段（保留段映射，慢）", "segments"),
                                                ("整篇（不保留时间映射）", "all"),
                                            ],
                                            value="refined",
                                        )
                                        ft_llm_template = gr.Dropdown(
                                            label="校对模板",
                                            choices=[("默认", "default"), ("严格保真", "strict"), ("会议", "meeting")],
                                            value="strict",
                                        )
                                    ft_enable_summary = gr.Checkbox(
                                        label="生成纪要",
                                        value=False,
                                    )
                                    ft_summary_llm_select = gr.Dropdown(
                                        label="纪要 LLM",
                                        choices=get_llm_choices(),
                                        value=get_default_llm_value(),
                                    )
                                    with gr.Row():
                                        ft_summary_scope = gr.Radio(
                                            label="纪要输入",
                                            choices=[("优先校对文本", "refined"), ("原始转写", "original")],
                                            value="refined",
                                        )
                                        ft_summary_template = gr.Dropdown(
                                            label="纪要模板",
                                            choices=[("默认", "default"), ("严格保真", "strict"), ("会议纪要", "meeting")],
                                            value="meeting",
                                        )
                                    ft_enable_mindmap = gr.Checkbox(
                                        label="生成思维导图",
                                        value=False,
                                    )
                                    ft_mindmap_llm_select = gr.Dropdown(
                                        label="思维导图 LLM",
                                        choices=get_llm_choices(),
                                        value=get_default_llm_value(),
                                        info="在 .env 文件中配置 LLM providers，复制 .env.sample 为 .env 修改",
                                    )
                                    with gr.Row():
                                        ft_mindmap_scope = gr.Radio(
                                            label="思维导图输入",
                                            choices=[("优先校对文本", "refined"), ("原始转写", "original")],
                                            value="refined",
                                        )
                                        ft_mindmap_template = gr.Dropdown(
                                            label="思维导图模板",
                                            choices=[("默认", "default"), ("严格保真", "strict"), ("会议结构", "meeting")],
                                            value="meeting",
                                        )

                                with gr.Accordion("8. 翻译、情感与导出", open=False):
                                    ft_translation_enabled = gr.Checkbox(label="启用翻译", value=False)
                                    ft_translation_model = gr.Dropdown(
                                        label="翻译模型",
                                        choices=["nllb-200-distilled-600m", "nllb-200-distilled-1.3b"],
                                        value="nllb-200-distilled-600m",
                                    )
                                    with gr.Row():
                                        ft_source_lang = gr.Textbox(label="源语言代码", value="zho_Hans")
                                        ft_target_lang = gr.Textbox(label="目标语言代码", value="eng_Latn")
                                    ft_emotion_enabled = gr.Checkbox(label="启用情感识别", value=False)
                                    with gr.Row():
                                        ft_emotion_model = gr.Dropdown(
                                            label="情感模型",
                                            choices=emotion_model_choices,
                                            value=choose_default_emotion_model(emotion_model_choices) or DEFAULT_EMOTION_MODEL,
                                        )
                                        ft_emotion_granularity = gr.Radio(label="情感粒度", choices=["utterance", "frame"], value="utterance")
                                    ft_export_formats = gr.CheckboxGroup(
                                        label="导出格式",
                                        choices=["json", "txt", "srt", "vtt", "tsv", "all"],
                                        value=["json", "txt", "srt"],
                                    )
                                    with gr.Row():
                                        ft_include_raw_candidates = gr.Checkbox(label="导出全部模型候选", value=True)
                                        ft_include_config_snapshot = gr.Checkbox(label="导出配置快照", value=True)

                                ft_run_btn = gr.Button("🚀 提交精细转录工作流", variant="primary")
                                ft_cancel_btn = gr.Button("取消当前任务", variant="stop")
                                ft_legacy_run_btn = gr.Button("兼容旧管线", visible=False)

                            # 右列：结果展示
                            with gr.Column(scale=2, min_width=600):
                                ft_status = gr.Textbox(
                                    label="实时状态",
                                    lines=3,
                                    interactive=False,
                                )
                                ft_event_log = gr.HTML(
                                    value=render_workflow_event_panel([]),
                                )

                                with gr.Accordion("音字联动", open=True):
                                    ft_audio_sync = gr.HTML(
                                        value=get_audio_sync_html(),
                                    )

                                with gr.Accordion("转写文本", open=True):
                                    ft_transcript = gr.Textbox(
                                        label="精细转录文本",
                                        lines=15,
                                        max_lines=30,
                                        interactive=False,
                                    )

                                with gr.Accordion("会议纪要", open=True):
                                    ft_summary = gr.Markdown("")

                                with gr.Accordion("思维导图", open=True):
                                    ft_mindmap = gr.HTML(value="")

                                with gr.Accordion("下载文件", open=False):
                                    with gr.Row():
                                        ft_download_json = gr.File(label="下载 JSON", visible=True)
                                        ft_download_txt = gr.File(label="下载 TXT", visible=True)
                                        ft_download_srt = gr.File(label="下载 SRT", visible=True)
                                    with gr.Row():
                                        ft_download_vtt = gr.File(label="下载 VTT", visible=True)
                                        ft_download_tsv = gr.File(label="下载 TSV", visible=True)
                                        ft_download_zip = gr.File(label="下载 ZIP", visible=True)

                                ft_result_state = gr.State(value=None)

                        # 场景详情弹窗：打开
                        def _on_open_scene_modal(scene_id):
                            """打开场景详情弹窗，填充预设信息"""
                            t = get_template(scene_id)
                            if t:
                                hotwords_str = "\n".join(t.hotwords)
                                return gr.update(visible=True), t.name, t.description, t.llm_prompt, hotwords_str
                            return gr.update(visible=True), "", "", "", ""

                        ft_scene_btn.click(
                            fn=_on_open_scene_modal,
                            inputs=[ft_scene],
                            outputs=[ft_scene_modal, ft_scene_name, ft_scene_desc_text, ft_scene_prompt, ft_scene_hotwords_display],
                        )

                        # 场景详情弹窗：关闭
                        ft_scene_close_btn.click(
                            fn=lambda: gr.update(visible=False),
                            outputs=[ft_scene_modal],
                        )

                        # 词表弹窗：打开（从 state 读取，填入弹窗内文本框）
                        def _on_open_hotword_modal(scene_id, hotwords_state):
                            """打开词表弹窗，填充预设热词和已有自定义热词"""
                            t = get_template(scene_id)
                            preset_str = "\n".join(t.hotwords) if t else ""
                            return gr.update(visible=True), preset_str, hotwords_state or ""

                        ft_hotword_btn.click(
                            fn=_on_open_hotword_modal,
                            inputs=[ft_scene, ft_hotwords_state],
                            outputs=[ft_hotword_modal, ft_preset_hotwords, ft_custom_hotwords],
                        )

                        # 词表弹窗：关闭（把弹窗内编辑的值存回 state）
                        def _on_close_hotword_modal(custom_hotwords_text):
                            """关闭词表弹窗，保存编辑后的自定义热词到 state"""
                            return gr.update(visible=False), custom_hotwords_text or ""

                        ft_hotword_close_btn.click(
                            fn=_on_close_hotword_modal,
                            inputs=[ft_custom_hotwords],
                            outputs=[ft_hotword_modal, ft_hotwords_state],
                        )

                        # 执行精细转录（流式：逐块 yield 中间结果；最终一次性补全下载文件）
                        def _on_run_pipeline(
                            audio_path, scene_id, model, enable_preprocess,
                            enable_llm, enable_summary, enable_mindmap,
                            custom_hotwords, llm_select, base_url,
                            scene_prompt,
                        ):
                            """精细转录执行按钮回调（流式生成器）

                            Gradio 要求 outputs 的顺序与每次 yield 顺序一致，共 12 项：
                            [status, sync_html, transcript, summary_md, mindmap_html, result_state,
                             json_file, txt_file, srt_file, vtt_file, tsv_file, zip_file]
                            """
                            # 初始占位：保证输出数量一致
                            def _empty_outputs(status_text, sync_html="", transcript="",
                                               summary_md="", mindmap_html="", result_state=None,
                                               json_file=None, txt_file=None, srt_file=None,
                                               vtt_file=None, tsv_file=None, zip_file=None):
                                return [
                                    status_text, sync_html, transcript, summary_md, mindmap_html, result_state,
                                    json_file, txt_file, srt_file, vtt_file, tsv_file, zip_file,
                                ]

                            if not audio_path:
                                yield _empty_outputs("❌ 请先上传音频文件")
                                return
                            # 从 .env 解析选中的 LLM 配置
                            llm_cfg_model = get_llm_by_value(llm_select)
                            if llm_cfg_model:
                                llm_cfg, llm_model = llm_cfg_model
                                llm_url = llm_cfg.base_url
                                llm_api_key = llm_cfg.api_key
                            else:
                                llm_url = "http://127.0.0.1:11434/v1"
                                llm_model = "qwen2.5:7b"
                                llm_api_key = "no-key"

                            # 记录中间态：asr_chunk 时更新文本，LLM/summary/mindmap 到来时对应更新
                            last_sync_html = get_audio_sync_html()
                            last_transcript = ""
                            last_summary_md = ""
                            last_mindmap_html = ""
                            last_result = None
                            warnings_log: list = []  # 累积 LLM 失败警告，附在 status 末尾
                            def _status_with_warnings(main: str) -> str:
                                if not warnings_log:
                                    return main
                                return main + "\n\n⚠️ 警告:\n" + "\n".join(f"- {w}" for w in warnings_log)

                            try:
                                for stage, payload in run_pipeline_streaming(
                                    audio_path=audio_path,
                                    scene_id=scene_id,
                                    model=model,
                                    enable_preprocess=enable_preprocess,
                                    enable_llm_refine=enable_llm,
                                    enable_summary=enable_summary,
                                    enable_mindmap=enable_mindmap,
                                    custom_hotwords=custom_hotwords,
                                    asr_base_url=base_url,
                                    llm_base_url=llm_url,
                                    llm_model=llm_model,
                                    llm_api_key=llm_api_key or "no-key",
                                    custom_prompt=scene_prompt if scene_prompt else None,
                                ):
                                    if stage == "progress":
                                        prog = payload.get("progress", 0)
                                        desc = payload.get("desc", "")
                                        status = _status_with_warnings(
                                            f"⏳ {desc} (进度 {int(prog * 100)}%)"
                                        )
                                        yield _empty_outputs(
                                            status, sync_html=last_sync_html, transcript=last_transcript,
                                            summary_md=last_summary_md, mindmap_html=last_mindmap_html,
                                            result_state=last_result,
                                        )
                                        continue

                                    if stage == "warning":
                                        # LLM 失败或熔断激活：累积警告，更新 status 让用户知道卡的原因
                                        msg = payload.get("message") or "未知警告"
                                        stage_name = {
                                            "llm_refine": "优化",
                                            "summary": "纪要",
                                            "mindmap": "思维导图",
                                        }.get(payload.get("stage") or "", payload.get("stage") or "")
                                        chunk = payload.get("chunk"); total = payload.get("total")
                                        label = f"[{stage_name}"
                                        if chunk and total:
                                            label += f" {chunk}/{total}"
                                        label += "]"
                                        warnings_log.append(f"{label} {msg}")
                                        # 超过 12 条则只保留首尾，避免状态框撑爆
                                        if len(warnings_log) > 12:
                                            warnings_log[:] = warnings_log[:5] + [
                                                f"...(已省略 {len(warnings_log)-10} 条)"
                                            ] + warnings_log[-5:]
                                        status = _status_with_warnings("⏳ 继续执行（有部分 LLM 调用未返回）")
                                        yield _empty_outputs(
                                            status, sync_html=last_sync_html, transcript=last_transcript,
                                            summary_md=last_summary_md, mindmap_html=last_mindmap_html,
                                            result_state=last_result,
                                        )
                                        continue

                                    if stage == "asr_chunk":
                                        # ASR 分块完成：截至目前的合并结果，立即展示到文本框
                                        segments = payload.get("segments") or []
                                        raw_text = payload.get("text", "") or ""
                                        last_transcript = format_transcript_text(segments, "")
                                        # 音字联动注入（只有当没有 refined_text 时，显示原始 segments）
                                        segments_json = json_for_inline_script(segments)
                                        audio_url_json = json_for_inline_script(str(audio_path or ""))
                                        sync_script = f"""
                                        <script>
                                        (function() {{
                                            if (window.__audioSync) {{
                                                window.__audioSync.setAudioSrc({audio_url_json});
                                                window.__audioSync.renderTranscript({segments_json});
                                            }}
                                        }})();
                                        </script>
                                        """
                                        last_sync_html = get_audio_sync_html() + sync_script
                                        status = _status_with_warnings(
                                            f"🎙️ ASR 进行中 — 已识别 {len(segments)} 段 / {len(raw_text)} 字"
                                        )
                                        yield _empty_outputs(
                                            status, sync_html=last_sync_html, transcript=last_transcript,
                                            summary_md=last_summary_md, mindmap_html=last_mindmap_html,
                                            result_state=last_result,
                                        )
                                        continue

                                    if stage == "llm_refine":
                                        # LLM 润色：每次分块都会推送累积文本（最后一次带 final=True）
                                        accumulated = payload.get("text", "")
                                        is_final = bool(payload.get("final", False))
                                        last_transcript = accumulated or last_transcript
                                        status = _status_with_warnings(
                                            "🧠 LLM 优化中..." if not is_final else "✅ LLM 优化完成"
                                        )
                                        yield _empty_outputs(
                                            status, sync_html=last_sync_html, transcript=last_transcript,
                                            summary_md=last_summary_md, mindmap_html=last_mindmap_html,
                                            result_state=last_result,
                                        )
                                        continue

                                    if stage == "summary":
                                        # 纪要生成（分块阶段也会推送 interim 结果）
                                        last_summary_md = format_summary_display(payload)
                                        status = _status_with_warnings("📝 纪要生成中(阶段性结果)")
                                        yield _empty_outputs(
                                            status, sync_html=last_sync_html, transcript=last_transcript,
                                            summary_md=last_summary_md, mindmap_html=last_mindmap_html,
                                            result_state=last_result,
                                        )
                                        continue

                                    if stage == "mindmap":
                                        # 思维导图生成：无论是否为空，都给兜底显示，让用户"看得到"
                                        mindmap_data = payload or {}
                                        if mindmap_data:
                                            mindmap_html = get_markmap_html(
                                                __import__("json").dumps(mindmap_data, ensure_ascii=False)
                                            )
                                        else:
                                            mindmap_html = """
                                            <div style="padding:16px; background:#fff8e1; border:1px dashed #ffca28; border-radius:8px; color:#b26a00;">
                                              <b>思维导图尚未生成</b><br/>
                                              可能原因：文本长度不足、LLM 未返回结构化 JSON、或当前场景未配置思维导图提示词。
                                              <br/>请检查 .env 中 LLM provider 连通性，或重新执行。
                                            </div>
                                            """
                                        last_mindmap_html = mindmap_html
                                        status = _status_with_warnings("🗺️ 思维导图生成中(阶段性结果)")
                                        yield _empty_outputs(
                                            status, sync_html=last_sync_html, transcript=last_transcript,
                                            summary_md=last_summary_md, mindmap_html=last_mindmap_html,
                                            result_state=last_result,
                                        )
                                        continue

                                    if stage == "error":
                                        msg = payload.get("message", "未知错误")
                                        yield _empty_outputs(
                                            _status_with_warnings(f"❌ 执行失败: {msg}"),
                                            sync_html=last_sync_html, transcript=last_transcript,
                                            summary_md=last_summary_md, mindmap_html=last_mindmap_html,
                                            result_state=last_result,
                                        )
                                        return

                                    if stage == "final":
                                        # 最终结果：格式化 + 生成下载文件（包含纪要/思维导图）
                                        result = payload
                                        transcript_text = format_transcript_text(
                                            result.get("segments", []),
                                            result.get("refined_text", ""),
                                        )
                                        summary_md = format_summary_display(result.get("summary", {}))
                                        mindmap_data = result.get("mindmap", {})
                                        if mindmap_data:
                                            mindmap_html = get_markmap_html(
                                                __import__("json").dumps(mindmap_data, ensure_ascii=False)
                                            )
                                        else:
                                            mindmap_html = last_mindmap_html or """
                                            <div style="padding:16px; background:#fff8e1; border:1px dashed #ffca28; border-radius:8px; color:#b26a00;">
                                              <b>思维导图尚未生成</b>
                                            </div>
                                            """
                                        # 音字联动
                                        segments_json = json_for_inline_script(result.get("segments", []))
                                        audio_url = result.get("audio_path", "") or audio_path
                                        audio_url_json = json_for_inline_script(str(audio_url or ""))
                                        sync_script = f"""
                                        <script>
                                        (function() {{
                                            if (window.__audioSync) {{
                                                window.__audioSync.setAudioSrc({audio_url_json});
                                                window.__audioSync.renderTranscript({segments_json});
                                            }}
                                        }})();
                                        </script>
                                        """
                                        sync_html = get_audio_sync_html() + sync_script
                                        status = _status_with_warnings(
                                            f"✅ 完成 — 场景: {result.get('scene_name','')} | "
                                            f"耗时: {result.get('elapsed',0):.1f}s | ASR+LLM 协同"
                                        )

                                        # 生成多格式导出文件（精细转录专属：TXT/JSON/ZIP 包含 转写+纪要+思维导图）
                                        export_payload = {
                                            "text": result.get("raw_text", ""),
                                            "segments": result.get("segments", []),
                                            "refined_text": result.get("refined_text", ""),
                                            "summary": result.get("summary", {}),
                                            "mindmap": result.get("mindmap", {}),
                                            "scene_name": result.get("scene_name", ""),
                                            "elapsed": result.get("elapsed", 0),
                                        }
                                        exports = build_fine_export_files(export_payload)

                                        last_result = result
                                        yield [
                                            status, sync_html, transcript_text, summary_md, mindmap_html, result,
                                            exports.get("json"), exports.get("txt"), exports.get("srt"),
                                            exports.get("vtt"), exports.get("tsv"), exports.get("all"),
                                        ]
                                        return

                            except Exception as e:
                                yield _empty_outputs(
                                    f"❌ 执行失败: {e}", sync_html=last_sync_html, transcript=last_transcript,
                                    summary_md=last_summary_md, mindmap_html=last_mindmap_html,
                                    result_state=last_result,
                                )
                                return

                        workflow_value_keys = [
                            "preprocess_enabled", "noise_reduction", "noise_strength", "sample_rate", "loudnorm", "silence_mode",
                            "vad_enabled", "vad_preset", "chunk_enabled", "chunk_seconds", "overlap_seconds",
                            "primary_model", "transcription_mode", "reviewer_models", "primary_weight", "reviewer_weight",
                            "execution", "max_concurrency", "resource_failure_policy", "language", "use_itn", "punc_mode",
                            "timestamp_level", "forced_alignment", "aligner_model",
                            "diarization_enabled", "diarization_strategy", "diarization_asr_model", "speaker_model", "spk_mode",
                            "preset_speaker_count", "global_speaker_clustering",
                            "reconciliation_mode", "disagreement_threshold", "keep_alternatives", "uncertain_policy",
                            "llm_proofread_enabled", "llm_proofread_selection", "llm_proofread_scope", "llm_proofread_template_id",
                            "summary_enabled", "summary_selection", "summary_scope", "summary_template_id",
                            "mindmap_enabled", "mindmap_selection", "mindmap_scope", "mindmap_template_id",
                            "translation_enabled", "translation_model", "source_lang", "target_lang",
                            "emotion_enabled", "emotion_model", "emotion_granularity",
                            "export_formats", "include_raw_candidates", "include_config_snapshot",
                        ]
                        workflow_value_components = [
                            ft_enable_preprocess, ft_noise_reduction, ft_noise_strength, ft_sample_rate, ft_loudnorm, ft_silence_mode,
                            ft_vad_enabled, ft_vad_preset, ft_chunk_enabled, ft_chunk_seconds, ft_overlap_seconds,
                            ft_model, ft_transcription_mode, ft_reviewer_models, ft_primary_weight, ft_reviewer_weight,
                            ft_execution, ft_max_concurrency, ft_failure_policy, ft_language, ft_use_itn, ft_punc_mode,
                            ft_timestamp_level, ft_forced_alignment, ft_aligner_model,
                            ft_diarization_enabled, ft_diarization_strategy, ft_diarization_asr_model, ft_speaker_model, ft_spk_mode,
                            ft_preset_speaker_count, ft_global_speaker_clustering,
                            ft_reconciliation_mode, ft_disagreement_threshold, ft_keep_alternatives, ft_uncertain_policy,
                            ft_enable_llm, ft_llm_select, ft_llm_scope, ft_llm_template,
                            ft_enable_summary, ft_summary_llm_select, ft_summary_scope, ft_summary_template,
                            ft_enable_mindmap, ft_mindmap_llm_select, ft_mindmap_scope, ft_mindmap_template,
                            ft_translation_enabled, ft_translation_model, ft_source_lang, ft_target_lang,
                            ft_emotion_enabled, ft_emotion_model, ft_emotion_granularity,
                            ft_export_formats, ft_include_raw_candidates, ft_include_config_snapshot,
                        ]

                        def _workflow_outputs(
                            status, event_log="", sync_html="", transcript="", summary_md="", mindmap_html="",
                            state=None, json_file=None, txt_file=None, srt_file=None, vtt_file=None, tsv_file=None, zip_file=None,
                        ):
                            return [
                                status, event_log, sync_html, transcript, summary_md, mindmap_html, state,
                                json_file, txt_file, srt_file, vtt_file, tsv_file, zip_file,
                            ]

                        def _on_run_workflow(audio_path, api_base_url, request_timeout, scene_id, custom_hotwords, *component_values):
                            """提交异步工作流并轮询追加式事件，直至完成、失败或取消。"""
                            if not audio_path:
                                yield _workflow_outputs("❌ 请先上传音频文件")
                                return
                            values = dict(zip(workflow_value_keys, component_values))
                            values["preset_id"] = scene_id or "custom"
                            values["hotword"] = str(custom_hotwords or "").replace("\n", ",")
                            speaker_count = values.get("preset_speaker_count")
                            if speaker_count in {"", 0, 0.0}:
                                values["preset_speaker_count"] = None
                            elif speaker_count is not None:
                                values["preset_speaker_count"] = int(speaker_count)
                            config = build_workflow_config(values)
                            normalized_base_url = str(api_base_url or DEFAULT_BASE_URL).rstrip("/")
                            try:
                                validation = post_json_payload(
                                    f"{normalized_base_url}/v1/funasr/workflows/validate",
                                    config,
                                    request_timeout,
                                )
                                if not validation.get("valid"):
                                    messages = [
                                        f"{item.get('path', 'workflow')}: {item.get('message', '')} [{item.get('code', '')}]"
                                        for item in validation.get("errors") or []
                                    ]
                                    yield _workflow_outputs(
                                        "❌ 工作流配置校验失败",
                                        render_workflow_event_panel([], messages),
                                    )
                                    return
                                warning_lines = [
                                    f"WARNING {item.get('path', '')}: {item.get('message', '')} ({item.get('code', '')})"
                                    for item in validation.get("warnings") or []
                                ]
                                submitted = submit_workflow_job(
                                    normalized_base_url,
                                    audio_path,
                                    config,
                                    request_timeout,
                                )
                                job_id = str(submitted["job_id"])
                                state = {"job_id": job_id, "config": config}
                                accumulated_events: list[dict] = []
                                after_event_id = 0
                                yield _workflow_outputs(
                                    f"⏳ 任务 {job_id} 已提交",
                                    render_workflow_event_panel([], warning_lines),
                                    get_audio_sync_html(),
                                    state=state,
                                )

                                while True:
                                    snapshot = request_json(
                                        f"{normalized_base_url}/v1/funasr/workflows/{urllib.parse.quote(job_id)}",
                                        request_timeout,
                                    )
                                    event_payload = request_json(
                                        f"{normalized_base_url}/v1/funasr/workflows/{urllib.parse.quote(job_id)}/events?after_event_id={after_event_id}",
                                        request_timeout,
                                    )
                                    new_events = list(event_payload.get("data") or [])
                                    if new_events:
                                        accumulated_events.extend(new_events)
                                        after_event_id = max(int(item.get("event_id", 0)) for item in accumulated_events)
                                    log_text = render_workflow_event_panel(
                                        accumulated_events,
                                        warning_lines,
                                    )
                                    progress = int(float(snapshot.get("progress") or 0) * 100)
                                    current_stage = snapshot.get("current_stage") or "workflow"
                                    current_model = snapshot.get("current_model") or ""
                                    model_text = f" | 模型: {current_model}" if current_model else ""
                                    status = str(snapshot.get("status") or "running")
                                    status_text = f"⏳ {progress}% | 阶段: {current_stage}{model_text} | 任务: {job_id}"
                                    if status not in {"completed", "failed", "cancelled"}:
                                        yield _workflow_outputs(
                                            status_text,
                                            log_text,
                                            get_audio_sync_html(),
                                            state=state,
                                        )
                                        time.sleep(0.5)
                                        continue

                                    if status != "completed":
                                        error = snapshot.get("error") or "任务未完成"
                                        yield _workflow_outputs(
                                            f"❌ {status}: {error}",
                                            log_text,
                                            get_audio_sync_html(),
                                            state=state,
                                        )
                                        return

                                    result = dict(snapshot.get("result") or {})
                                    state.update({"result": result, "status": status})
                                    segments = list(result.get("segments") or [])
                                    transcript_text = format_transcript_text(
                                        segments,
                                        str(result.get("refined_text") or ""),
                                    )
                                    summary_md = format_summary_display(result.get("summary") or {})
                                    mindmap = result.get("mindmap") or {}
                                    mindmap_html = (
                                        get_markmap_html(json.dumps(mindmap, ensure_ascii=False)) if mindmap else ""
                                    )
                                    segments_json = json_for_inline_script(segments)
                                    audio_path_json = json_for_inline_script(str(audio_path or ""))
                                    sync_script = f"""
                                    <script>(function() {{
                                      if (window.__audioSync) {{
                                        window.__audioSync.setAudioSrc({audio_path_json});
                                        window.__audioSync.renderTranscript({segments_json});
                                      }}
                                    }})();</script>
                                    """
                                    downloads = build_workflow_downloads(
                                        normalized_base_url,
                                        job_id,
                                        result,
                                        request_timeout,
                                    )
                                    yield _workflow_outputs(
                                        f"✅ 100% | 工作流完成 | 任务: {job_id}",
                                        log_text,
                                        get_audio_sync_html() + sync_script,
                                        transcript_text,
                                        summary_md,
                                        mindmap_html,
                                        state,
                                        downloads.get("json"),
                                        downloads.get("txt"),
                                        downloads.get("srt"),
                                        downloads.get("vtt"),
                                        downloads.get("tsv"),
                                        downloads.get("all"),
                                    )
                                    return
                            except Exception as error:
                                yield _workflow_outputs(f"❌ 工作流执行失败：{error}")

                        def _cancel_workflow(api_base_url, request_timeout, state):
                            job_id = state.get("job_id") if isinstance(state, dict) else ""
                            if not job_id:
                                return "当前没有可取消的工作流任务"
                            try:
                                post_json(
                                    f"{str(api_base_url or DEFAULT_BASE_URL).rstrip('/')}/v1/funasr/workflows/{urllib.parse.quote(job_id)}/cancel",
                                    request_timeout,
                                )
                                return f"已向任务 {job_id} 发送取消请求"
                            except Exception as error:
                                return f"取消任务失败：{error}"

                        ft_run_btn.click(
                            fn=_on_run_workflow,
                            inputs=[ft_audio, base_url, timeout, ft_scene, ft_hotwords_state, *workflow_value_components],
                            outputs=[
                                ft_status, ft_event_log, ft_audio_sync, ft_transcript, ft_summary, ft_mindmap,
                                ft_result_state, ft_download_json, ft_download_txt, ft_download_srt,
                                ft_download_vtt, ft_download_tsv, ft_download_zip,
                            ],
                        )
                        ft_cancel_btn.click(
                            fn=_cancel_workflow,
                            inputs=[base_url, timeout, ft_result_state],
                            outputs=[ft_status],
                        )

                        ft_legacy_run_btn.click(
                            fn=_on_run_pipeline,
                            inputs=[
                                ft_audio, ft_scene, ft_model, ft_enable_preprocess,
                                ft_enable_llm, ft_enable_summary, ft_enable_mindmap,
                                ft_hotwords_state, ft_llm_select, base_url,
                                ft_scene_prompt,
                            ],
                            outputs=[
                                ft_status, ft_audio_sync, ft_transcript, ft_summary, ft_mindmap, ft_result_state,
                                ft_download_json, ft_download_txt, ft_download_srt,
                                ft_download_vtt, ft_download_tsv, ft_download_zip,
                            ],
                        )


                    with gr.Tab("说话人时间轴", render_children=False) as diarization_tab:
                        with gr.Row():
                            diarization_media_file = gr.File(
                                label="音频/视频文件",
                                type="filepath",
                                file_types=list(MEDIA_FILE_SUFFIXES),
                            )
                            with gr.Column():
                                diarization_model = gr.Dropdown(
                                    label="说话人分离模型",
                                    choices=diarization_model_choices,
                                    value=default_diarization_model_value,
                                )
                                diarization_model_source_hint = gr.HTML(
                                    value=get_model_source_hint_html(model_status_text),
                                    show_label=False
                                )
                        with gr.Row():
                            diarization_preview = gr.Video(
                                label="视频预览",
                                visible=False,
                                height=260,
                                elem_classes=["pat-media-preview"],
                            )
                            diarization_audio_preview = gr.Audio(label="音频预览", visible=False)
                            diarization_media_status = gr.Markdown("当前支持 paraformer / fun-asr-nano / sensevoice + cam++ 组合。")
                        with gr.Row():
                            diarization_spk_model = gr.Dropdown(
                                label="说话人模型(spk_model)",
                                choices=[("cam++", "cam++")],
                                value="cam++",
                            )
                            diarization_spk_mode = gr.Dropdown(
                                label="说话人模式(spk_mode)",
                                choices=[
                                    ("punc_segment", "punc_segment"),
                                    ("vad_segment", "vad_segment"),
                                    ("default", "default"),
                                ],
                                value="punc_segment",
                            )
                            diarization_preset_spk_num = gr.Number(label="预设说话人数(preset_spk_num)", value=0, precision=0)
                            diarization_button = gr.Button("开始说话人分离", variant="primary")
                        diarization_summary = gr.Textbox(label="说话人结果", lines=6, max_lines=12)
                        diarization_payload_state = gr.State("{}")
                        diarization_preview_format = gr.Radio(
                            label="预览格式",
                            choices=PREVIEW_FORMAT_CHOICES,
                            value=DEFAULT_PREVIEW_FORMAT,
                        )
                        diarization_preview_text = gr.Textbox(label="结果预览", lines=12, max_lines=24, buttons=["copy"])
                        with gr.Row():
                            diarization_download_json = gr.File(label="下载 JSON", visible=True)
                            diarization_download_txt = gr.File(label="下载 TXT", visible=True)
                            diarization_download_srt = gr.File(label="下载 SRT", visible=True)
                        with gr.Row():
                            diarization_download_vtt = gr.File(label="下载 VTT", visible=True)
                            diarization_download_tsv = gr.File(label="下载 TSV", visible=True)
                            diarization_download_zip = gr.File(label="下载 ZIP", visible=True)


            with gr.Tab("实时识别", render_children=False) as streaming_tab:
                with gr.Row(equal_height=False):
                    with gr.Column(scale=1, min_width=320):
                        stream_model = gr.Dropdown(
                            label="流式模型",
                            choices=streaming_model_choices,
                            value=default_streaming_model_value,
                        )
                        stream_model_source_hint = gr.HTML(
                            value=get_model_source_hint_html(model_status_text),
                            show_label=False
                        )
                    with gr.Column(scale=1, min_width=520):
                        with gr.Accordion("流式参数", open=False):
                            with gr.Row():
                                stream_chunk_size = gr.Textbox(label="分块大小(chunk_size)", value="0,30,15")
                                stream_encoder_lb = gr.Number(label="编码器回看帧数(encoder_chunk_look_back)", value=4, precision=0)
                                stream_decoder_lb = gr.Number(label="解码器回看帧数(decoder_chunk_look_back)", value=1, precision=0)
                # 文件与麦克风分开按需挂载，避免仅进入实时识别就初始化浏览器音频设备。
                with gr.Tabs():
                    with gr.Tab("文件流式识别", render_children=False):
                        gr.Markdown("### 文件流式识别", elem_classes=["pat-compact-markdown"])
                        stream_media_file = gr.File(
                            label="音频/视频文件",
                            type="filepath",
                            file_types=list(MEDIA_FILE_SUFFIXES),
                        )
                        stream_button = gr.Button("开始流式识别", variant="primary")
                        stream_file_stop_button = gr.Button("停止文件识别", variant="secondary")
                        stream_preview = gr.Video(
                            label="视频预览",
                            visible=False,
                            height=220,
                            elem_classes=["pat-media-preview"],
                        )
                        stream_audio_preview = gr.Audio(
                            label="音频预览",
                            sources=["upload"],
                            interactive=False,
                            visible=False,
                        )
                        stream_status = gr.Textbox(label="文件识别状态", interactive=False)
                        stream_transcript = gr.Textbox(label="文件流式输出", lines=8, max_lines=18, buttons=["copy"])
                        stream_download_button = gr.Button("生成结果下载", variant="secondary")
                        stream_download = gr.File(label="下载结果", visible=True)
                    with gr.Tab("Mic 实时识别", render_children=False):
                        gr.Markdown("### Mic 实时识别", elem_classes=["pat-compact-markdown"])
                        gr.Markdown(
                            get_system_microphone_runtime_status(),
                            elem_classes=["pat-compact-markdown"],
                        )
                        with gr.Row():
                            system_mic_device = gr.Dropdown(
                                label="系统输入设备",
                                choices=list_system_microphone_device_choices(),
                                value=SYSTEM_MIC_DEFAULT_DEVICE_VALUE,
                            )
                            system_mic_refresh_button = gr.Button("刷新输入设备", variant="secondary")
                        system_mic_toggle_button = gr.Button("开始录制并识别", variant="primary")
                        mic_status = gr.Textbox(label="麦克风识别状态", interactive=False, lines=2)
                        mic_signal = gr.Textbox(label="系统麦克风信号", interactive=False, lines=2)
                        mic_transcript = gr.Textbox(label="Mic 流式输出", lines=8, max_lines=18, buttons=["copy"])
                        mic_download_button = gr.Button("生成 Mic 结果下载", variant="secondary")
                        mic_download = gr.File(label="Mic 下载结果", visible=True)
                        system_mic_poll_timer = gr.Timer(value=0.6)
                stream_state = gr.State({})
                stream_mic_session = gr.State("")


            with gr.Tab("媒体与文本工具", render_children=False) as media_tools_workspace_tab:
                with gr.Tabs():
                    with gr.Tab("音频处理", render_children=False) as audio_tool_tab:
                        # 音频前处理工具。
                        with gr.Row(equal_height=False):
                            # 左列：参数面板
                            with gr.Column(scale=1, min_width=340):
                                gr.Markdown("### 音频前处理\n降噪 · 重采样 · VAD 裁剪 · 音量归一化")

                                audio_tool_input = gr.Audio(
                                    label="上传音频文件",
                                    type="filepath",
                                    sources=["upload"],
                                )

                                with gr.Accordion("前处理参数", open=True):
                                    with gr.Row():
                                        at_nr_enable = gr.Checkbox(
                                            label="降噪 (afftdn)",
                                            value=True,
                                        )
                                        at_nr_strength = gr.Slider(
                                            label="降噪强度 (dB)",
                                            minimum=3,
                                            maximum=48,
                                            value=12,
                                            step=1,
                                        )

                                    at_sample_rate = gr.Dropdown(
                                        label="目标采样率",
                                        choices=[("8000 Hz", 8000), ("16000 Hz (ASR)", 16000), ("22050 Hz", 22050), ("44100 Hz (CD)", 44100), ("48000 Hz (高保真)", 48000)],
                                        value=16000,
                                    )

                                    with gr.Row():
                                        at_vad_enable = gr.Checkbox(
                                            label="VAD 裁剪静音段",
                                            value=False,
                                        )
                                        at_loudnorm = gr.Checkbox(
                                            label="音量归一化",
                                            value=True,
                                        )

                                at_process_btn = gr.Button(
                                    "🚀 开始处理", variant="primary"
                                )

                            # 右列：结果展示
                            with gr.Column(scale=1, min_width=520):
                                at_info_before = gr.Textbox(
                                    label="处理前音频信息",
                                    lines=6,
                                    max_lines=10,
                                    interactive=False,
                                )
                                at_info_after = gr.Textbox(
                                    label="处理后音频信息",
                                    lines=6,
                                    max_lines=10,
                                    interactive=False,
                                )
                                at_output_audio = gr.Audio(
                                    label="处理后音频预览",
                                    type="filepath",
                                    sources=["upload"],
                                    interactive=False,
                                    editable=False,
                                    visible=False,
                                )
                                at_download = gr.File(
                                    label="下载处理后的 WAV",
                                    visible=False,
                                )

                        # 按钮点击处理函数
                        def _on_process_audio(input_path, nr_enable, nr_strength, sample_rate, vad_enable, loudnorm_enable):
                            """音频前处理按钮回调。"""
                            if not input_path:
                                return "❌ 请先上传音频文件", "", gr.update(value=None, visible=False), gr.update(value=None, visible=False)
                            try:
                                output_path, info_before, info_after = preprocess_audio(
                                    input_path,
                                    noise_reduction=nr_enable,
                                    noise_strength=float(nr_strength),
                                    sample_rate=int(sample_rate),
                                    vad_enabled=vad_enable,
                                    loudnorm=loudnorm_enable,
                                )
                                return (
                                    format_audio_info(info_before),
                                    format_audio_info(info_after),
                                    gr.update(visible=True, value=output_path),
                                    gr.update(visible=True, value=output_path),
                                )
                            except Exception as e:
                                return f"❌ 处理失败: {e}", "", gr.update(value=None, visible=False), gr.update(value=None, visible=False)

                        at_process_btn.click(
                            fn=_on_process_audio,
                            inputs=[audio_tool_input, at_nr_enable, at_nr_strength, at_sample_rate, at_vad_enable, at_loudnorm],
                            outputs=[at_info_before, at_info_after, at_output_audio, at_download],
                        )


                    with gr.Tab("跨语言翻译", render_children=False) as translation_tab:
                        # 顶部：参数与上传控制区（左右排布）
                        with gr.Row():
                            with gr.Column(scale=1, min_width=320):
                                trans_model = gr.Dropdown(
                                    label="翻译模型",
                                    choices=[
                                        ("NLLB-200-Distilled 600M", "nllb-200-distilled-600m"),
                                        ("NLLB-200-Distilled 1.3B", "nllb-200-distilled-1.3b"),
                                    ],
                                    value="nllb-200-distilled-600m",
                                )
                                trans_model_source_hint = gr.HTML(
                                    value=get_model_source_hint_html(model_status_text),
                                    show_label=False
                                )
                                from translation_languages import TRANSLATION_LANGUAGES_UI
                                with gr.Row():
                                    trans_source_lang = gr.Dropdown(
                                        label="源语言",
                                        choices=TRANSLATION_LANGUAGES_UI,
                                        value="eng_Latn",
                                        scale=1,
                                    )
                                    trans_swap_btn = gr.Button("⇄", scale=0, min_width=44, size="sm")
                                    trans_target_lang = gr.Dropdown(
                                        label="目标语言",
                                        choices=TRANSLATION_LANGUAGES_UI,
                                        value="zho_Hans",
                                        scale=1,
                                    )
                                trans_auto_zh_punc = gr.Checkbox(label="自动替换为中文全角标点", value=False)

                                with gr.Accordion("高级生成参数", open=False):
                                    trans_num_beams = gr.Slider(
                                        label="Beam Search 束搜索宽度 (num_beams)",
                                        minimum=1,
                                        maximum=5,
                                        step=1,
                                        value=5,
                                        info="1为Greedy模式（最快）；5为默认Beam模式；越大质量越高但越慢"
                                    )
                                    trans_max_length = gr.Slider(
                                        label="最大翻译长度限制 (max_length)",
                                        minimum=128,
                                        maximum=1024,
                                        step=32,
                                        value=512,
                                    )

                            with gr.Column(scale=1, min_width=320):
                                trans_input_file = gr.File(
                                    label="上传文本或字幕文件 (可选，支持 .txt, .md, .srt, .vtt, .tsv, .json)",
                                    file_types=[".txt", ".md", ".srt", ".vtt", ".tsv", ".json"],
                                )
                                trans_button = gr.Button("开始翻译", variant="primary")

                        # 中部：原文与译文窗口（左右对齐、等高）
                        with gr.Row():
                            trans_input_text = gr.Textbox(
                                label="长文本输入 (原文)",
                                placeholder="请输入或粘贴需要翻译的文本内容...",
                                lines=20,
                                max_lines=20,
                            )
                            trans_output_text = gr.Textbox(
                                label="翻译结果 (译文)",
                                lines=20,
                                max_lines=20,
                                buttons=["copy"],
                            )

                        # 底部：结果下载
                        trans_result_file_state = gr.State(value=None)
                        with gr.Row():
                            trans_download_btn = gr.Button("📊 生成并导出文件", variant="secondary")
                            trans_download_file = gr.File(
                                label="下载翻译后的文件",
                                visible=False,
                            )


                    with gr.Tab("情感识别", render_children=False) as emotion_tab:
                        with gr.Row():
                            emotion_media_file = gr.File(
                                label="音频/视频文件",
                                type="filepath",
                                file_types=list(MEDIA_FILE_SUFFIXES),
                            )
                            with gr.Column():
                                emotion_model = gr.Dropdown(
                                    label="情感识别模型",
                                    choices=emotion_model_choices,
                                    value=default_emotion_model_value,
                                )
                                emotion_model_source_hint = gr.HTML(
                                    value=get_model_source_hint_html(model_status_text),
                                    show_label=False
                                )
                        with gr.Row():
                            emotion_preview = gr.Video(
                                label="视频预览",
                                visible=False,
                                height=260,
                                elem_classes=["pat-media-preview"],
                            )
                            emotion_audio_preview = gr.Audio(label="音频预览", visible=False)
                            emotion_media_status = gr.Markdown("当前先支持整体情感识别，后续再补时间片能力。")
                        with gr.Row():
                            emotion_granularity = gr.Dropdown(
                                label="情感粒度(granularity)",
                                choices=[("utterance", "utterance"), ("frame", "frame")],
                                value="utterance",
                            )
                            emotion_button = gr.Button("开始情感识别", variant="primary")
                        emotion_summary = gr.Textbox(label="情感结果", lines=4, max_lines=8)
                        emotion_raw_json = gr.Textbox(label="情感原始 JSON", lines=10, max_lines=20)


            with gr.Tab("模型与服务", render_children=False) as service_tab:
                gr.Markdown("模型与服务控制台：按总览、模型、资源、任务和诊断五个区域集中管理。")
                with gr.Tabs():
                    with gr.Tab("服务总览", render_children=False) as service_overview_tab:
                        gr.Markdown(f"- API：`{default_base_url}`\n- UI：默认 `7861/7862/7863` 自动择空闲端口")
                        with gr.Row():
                            check_button = gr.Button("检查服务", variant="secondary")
                            auto_refresh_logs = gr.Checkbox(label="自动刷新状态与日志(可能影响性能)", value=True)
                        service_overview = gr.Markdown(initial_overview_markdown)
                        capability_target = gr.Markdown(initial_target_markdown)

                    with gr.Tab("模型管理", render_children=False) as service_models_tab:
                        with gr.Row():
                            refresh_models_button = gr.Button("刷新模型列表", variant="secondary")
                            capability_filter = gr.Dropdown(
                                label="能力筛选",
                                choices=CAPABILITY_FILTER_CHOICES,
                                value="all",
                            )
                        model_status = gr.Textbox(label="模型摘要", value=model_status_text, interactive=False)
                        service_capability = gr.Markdown(initial_capability_markdown)

                    with gr.Tab("运行资源", render_children=False) as service_resources_tab:
                        refresh_runtime_button = gr.Button("刷新运行资源", variant="secondary")
                        runtime_resources = gr.Markdown("### 运行资源\n\n点击“刷新运行资源”加载。")
                        service_raw_json = gr.Textbox(label="服务 / 资源原始状态", lines=10, max_lines=20)

                    with gr.Tab("任务队列", render_children=False) as service_workflows_tab:
                        refresh_workflow_queue_button = gr.Button("刷新任务队列", variant="secondary")
                        workflow_queue_panel = gr.Markdown("### 任务队列\n\n点击“刷新任务队列”加载。")

                    with gr.Tab("诊断与日志", render_children=False) as service_diagnostics_tab:
                        with gr.Row():
                            log_max_lines = gr.Slider(label="日志行数", minimum=50, maximum=2000, step=50, value=120)
                            log_max_kb = gr.Slider(label="单文件读取上限(KB)", minimum=64, maximum=2048, step=64, value=256)
                            log_max_section_chars = gr.Slider(
                                label="单段显示上限(字符)", minimum=2000, maximum=40000, step=2000, value=8000,
                            )
                        with gr.Row():
                            refresh_logs_button = gr.Button("刷新运行日志", variant="secondary")
                            download_logs_button = gr.Button("打包下载运行日志", variant="secondary")
                        runtime_logs = gr.Textbox(
                            label="运行日志", value=initial_runtime_logs, lines=18, max_lines=30, interactive=False,
                        )
                        runtime_logs_archive = gr.File(label="日志下载", visible=True)
                runtime_log_timer = gr.Timer(value=5.0)

        check_button.click(
            fn=activate_and_refresh_service_tab,
            inputs=[base_url, timeout, capability_filter, log_max_lines, log_max_kb, log_max_section_chars],
            outputs=[
                service_tab_active,
                model_status,
                service_raw_json,
                service_overview,
                service_capability,
                capability_target,
                runtime_logs,
            ],
        )
        check_button.click(
            fn=safe_build_runtime_panels,
            inputs=[base_url, timeout],
            outputs=[runtime_resources, workflow_queue_panel],
        )
        refresh_runtime_button.click(
            fn=safe_build_runtime_panels,
            inputs=[base_url, timeout],
            outputs=[runtime_resources, workflow_queue_panel],
        )
        refresh_workflow_queue_button.click(
            fn=safe_build_runtime_panels,
            inputs=[base_url, timeout],
            outputs=[runtime_resources, workflow_queue_panel],
        )
        capability_filter.change(
            fn=safe_render_capabilities,
            inputs=[base_url, timeout, capability_filter],
            outputs=[service_overview, service_capability, capability_target],
        )
        refresh_models_button.click(
            fn=refresh_model_dropdown,
            inputs=[base_url, timeout],
            outputs=[
                model,
                stream_model,
                emotion_model,
                diarization_model,
                model_status,
                offline_model_source_hint,
                stream_model_source_hint,
                emotion_model_source_hint,
                diarization_model_source_hint,
                trans_model_source_hint,
            ],
        )
        refresh_logs_button.click(
            fn=read_runtime_logs_ui,
            inputs=[log_max_lines, log_max_kb, log_max_section_chars],
            outputs=[runtime_logs],
        )
        download_logs_button.click(
            fn=build_runtime_logs_archive,
            outputs=[runtime_logs_archive],
        )
        runtime_log_timer.tick(
            fn=auto_refresh_service_dashboard_guard,
            inputs=[auto_refresh_logs, service_tab_active, base_url, timeout, capability_filter, log_max_lines, log_max_kb, log_max_section_chars],
            outputs=[model_status, service_raw_json, service_overview, service_capability, capability_target, runtime_logs],
        )
        runtime_log_timer.tick(
            fn=auto_refresh_runtime_panels_guard,
            inputs=[auto_refresh_logs, service_tab_active, base_url, timeout],
            outputs=[runtime_resources, workflow_queue_panel],
        )
        # 业务导航 Tab 只负责前端切换，不绑定后端 select 回调，避免切换栏目时进入请求队列。
        for service_feature_tab in (
            service_overview_tab,
            service_models_tab,
            service_resources_tab,
            service_workflows_tab,
            service_diagnostics_tab,
        ):
            service_feature_tab.select(
                fn=lambda: set_service_tab_auto_refresh_active(True),
                outputs=[service_tab_active],
                queue=False,
                show_progress="hidden",
            )
        media_file.change(
            fn=update_media_preview,
            inputs=[media_file],
            outputs=[media_preview, media_audio_preview, media_status],
        )
        stream_media_file.change(
            fn=update_media_preview,
            inputs=[stream_media_file],
            outputs=[stream_preview, stream_audio_preview],
        )
        emotion_media_file.change(
            fn=update_media_preview,
            inputs=[emotion_media_file],
            outputs=[emotion_preview, emotion_audio_preview, emotion_media_status],
        )
        emotion_model.change(
            fn=update_emotion_granularity_options,
            inputs=[emotion_model],
            outputs=[emotion_granularity],
        )
        diarization_media_file.change(
            fn=update_media_preview,
            inputs=[diarization_media_file],
            outputs=[diarization_preview, diarization_audio_preview, diarization_media_status],
        )
        transcribe_button.click(
            fn=safe_transcribe_with_exports,
            inputs=[
                base_url,
                media_file,
                model,
                transcript_preview_format,
                timeout,
                language,
                hotword,
                vad_preset,
                merge_vad,
                use_itn,
                merge_length_s,
                max_line_width,
                batch_size_s,
                batch_size_threshold_s,
                vad_max_single_segment_time,
                punc_mode,
                device,
                hub,
                disable_update,
                ncpu,
                log_level,
                disable_pbar,
            ],
            outputs=[
                transcript,
                transcript_payload_state,
                download_json,
                download_txt,
                download_srt,
                download_vtt,
                download_tsv,
                download_zip,
            ],
        )
        batch_button.click(
            fn=batch_transcribe,
            inputs=[
                batch_files,
                base_url,
                model,
                batch_response_format,
                timeout,
                language,
                hotword,
                vad_preset,
                merge_vad,
                use_itn,
                merge_length_s,
                max_line_width,
                batch_size_s,
                batch_size_threshold_s,
                vad_max_single_segment_time,
                punc_mode,
                device,
                hub,
                disable_update,
                ncpu,
                log_level,
                disable_pbar,
            ],
            outputs=[batch_status, batch_download, failed_batch_state],
        )
        retry_failed_button.click(
            fn=retry_failed_batch,
            inputs=[
                failed_batch_state,
                base_url,
                model,
                batch_response_format,
                timeout,
                language,
                hotword,
                vad_preset,
                merge_vad,
                use_itn,
                merge_length_s,
                max_line_width,
                batch_size_s,
                batch_size_threshold_s,
                vad_max_single_segment_time,
                punc_mode,
                device,
                hub,
                disable_update,
                ncpu,
                log_level,
                disable_pbar,
            ],
            outputs=[batch_status, batch_download, failed_batch_state],
        )
        transcript_preview_format.change(
            fn=update_transcription_preview,
            inputs=[transcript_preview_format, transcript_payload_state],
            outputs=[transcript],
        )

        stream_file_event = stream_button.click(
            fn=stream_transcribe_file,
            inputs=[
                base_url,
                stream_media_file,
                stream_model,
                timeout,
                stream_chunk_size,
                stream_encoder_lb,
                stream_decoder_lb,
            ],
            outputs=[stream_transcript, stream_status],
        )
        system_mic_refresh_button.click(
            fn=refresh_system_microphone_device_dropdown,
            outputs=[system_mic_device],
            queue=False,
            show_progress="hidden",
        )
        system_mic_toggle_button.click(
            fn=toggle_system_microphone_stream,
            inputs=[
                stream_mic_session,
                base_url,
                stream_model,
                timeout,
                system_mic_device,
                stream_chunk_size,
                stream_encoder_lb,
                stream_decoder_lb,
            ],
            outputs=[stream_mic_session, mic_status, system_mic_toggle_button],
            queue=False,
            show_progress="hidden",
        )
        system_mic_poll_timer.tick(
            fn=poll_system_microphone_stream,
            inputs=[stream_mic_session],
            outputs=[mic_transcript, mic_status, mic_signal, stream_mic_session],
            queue=False,
            show_progress="hidden",
        )
        stream_file_stop_button.click(
            fn=stop_streaming_status,
            outputs=[stream_status],
            cancels=[stream_file_event],
        )
        stream_download_button.click(
            fn=build_streaming_download_file,
            inputs=[stream_transcript],
            outputs=[stream_download],
        )
        mic_download_button.click(
            fn=build_streaming_download_file,
            inputs=[mic_transcript],
            outputs=[mic_download],
        )
        emotion_button.click(
            fn=safe_recognize_emotion,
            inputs=[base_url, emotion_media_file, emotion_model, emotion_granularity, timeout],
            outputs=[emotion_summary, emotion_raw_json],
        )
        diarization_button.click(
            fn=safe_recognize_diarization_with_exports,
            inputs=[
                base_url,
                diarization_media_file,
                diarization_model,
                diarization_spk_model,
                diarization_spk_mode,
                diarization_preset_spk_num,
                diarization_preview_format,
                timeout,
            ],
            outputs=[
                diarization_summary,
                diarization_preview_text,
                diarization_payload_state,
                diarization_download_json,
                diarization_download_txt,
                diarization_download_srt,
                diarization_download_vtt,
                diarization_download_tsv,
                diarization_download_zip,
            ],
        )
        diarization_preview_format.change(
            fn=update_diarization_preview,
            inputs=[diarization_preview_format, diarization_payload_state],
            outputs=[diarization_preview_text],
        )
        # 绑定交换源与目标语言按钮
        trans_swap_btn.click(
            fn=lambda src, tgt: (tgt, src),
            inputs=[trans_source_lang, trans_target_lang],
            outputs=[trans_source_lang, trans_target_lang],
        )
        trans_button.click(
            fn=safe_translate_with_exports,
            inputs=[
                base_url,
                trans_input_text,
                trans_input_file,
                trans_source_lang,
                trans_target_lang,
                trans_model,
                timeout,
                trans_num_beams,
                trans_max_length,
                trans_auto_zh_punc,
            ],
            outputs=[trans_output_text, trans_result_file_state, trans_download_file],
        )
        trans_download_btn.click(
            fn=safe_export_translation_file,
            inputs=[
                trans_output_text,
                trans_result_file_state,
                trans_input_file,
                trans_source_lang,
                trans_target_lang,
            ],
            outputs=[trans_download_file],
        )

    return demo


def main() -> None:
    try:
        if os.name == "nt":
            import asyncio

            try:
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            except Exception:
                pass

            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            old_handler = loop.get_exception_handler()

            def _quiet_connection_reset(loop, context):
                exc = context.get("exception")
                if isinstance(exc, ConnectionResetError) and getattr(exc, "winerror", None) == 10054:
                    return
                if old_handler is not None:
                    old_handler(loop, context)
                else:
                    loop.default_exception_handler(context)

            loop.set_exception_handler(_quiet_connection_reset)
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Run a Gradio demo for the FunASR OpenAI-compatible API")
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", DEFAULT_BASE_URL), help="FunASR API base URL")
    parser.add_argument("--host", default=os.getenv("GRADIO_HOST", "127.0.0.1"), help="Gradio bind host")
    parser.add_argument("--port", type=int, default=int(os.getenv("GRADIO_PORT", "7861")), help="Gradio bind port")
    parser.add_argument("--timeout", type=float, default=float(os.getenv("TIMEOUT", "0")), help="HTTP timeout in seconds; <=0 disables timeout")
    parser.add_argument("--share", action="store_true", help="Create a temporary Gradio share link")
    args = parser.parse_args()

    app = build_app(args.base_url, args.timeout)
    app.launch(server_name=args.host, server_port=args.port, share=args.share, css=APP_CSS)


if __name__ == "__main__":
    main()
