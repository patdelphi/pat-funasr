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
import io
import json
import mimetypes
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
import subprocess

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

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_PREVIEW_FORMAT = "txt"
PREVIEW_FORMAT_CHOICES = ["json", "txt", "srt", "vtt", "tsv"]
DEFAULT_BATCH_RESPONSE_FORMAT = "all"
RUNTIME_LOG_FILENAMES = ("funasr-api.log", "funasr-ui.log", "funasr-single-window.log")

PREVIEW_MAX_CHARS = 8000
RAW_JSON_PREVIEW_MAX_CHARS = 12000
BATCH_ITEM_MESSAGE_MAX_CHARS = 240
STREAMING_PREVIEW_MAX_CHARS = 4000
STREAMING_UI_UPDATE_EVERY_CHUNKS = 3
STREAMING_UI_UPDATE_MIN_INTERVAL_S = 0.5
BATCH_RUNNING_STATUS_UPDATE_EVERY_ITEMS = 3


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
.pat-placeholder-box {
  border: 1px dashed #c7cfdd;
  border-radius: 10px;
  padding: 12px 14px;
  background: #fafbfc;
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
        yield "", "未找到 ffmpeg，无法进行 Streaming（需要用 ffmpeg 转 PCM）。"
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
                yield preview_text or full_text, f"Streaming failed: {error}"
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
    return limit_raw_json_preview(json.dumps({"health": health, "models": models}, ensure_ascii=False, indent=2))


def build_service_dashboard_snapshot(base_url: str, timeout: float, capability_filter: str) -> tuple[str, str, str, str, str]:
    """生成服务页自动刷新所需的轻量快照。"""
    base_url = base_url.rstrip("/")
    _, status_text, models = fetch_model_choices(base_url, timeout)
    health = request_json(f"{base_url}/health", timeout)
    raw_json = limit_raw_json_preview(json.dumps({"health": health, "models": models}, ensure_ascii=False, indent=2))
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
    paths = normalize_uploaded_paths(batch_files)
    if not paths:
        yield "请先上传至少一个批量文件。", None, []
        return

    results = initialize_batch_results(paths)
    yield summarize_batch_results(results), None, []

    for index, file_path in enumerate(paths):
        results[index]["status"] = "running"
        results[index]["message"] = ""
        if index % BATCH_RUNNING_STATUS_UPDATE_EVERY_ITEMS == 0:
            yield summarize_batch_results(results), None, [
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

        yield summarize_batch_results(results), None, [
            item["source_path"] for item in results if item.get("status") == "error"
        ]

    summary = summarize_batch_results(results)
    archive_path = build_batch_archive(results)
    failed_paths = [item["source_path"] for item in results if item.get("status") == "error"]
    yield summary, archive_path, failed_paths


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
        vad_max_single_segment_time=vad_max_single_segment_time,
        punc_mode=punc_mode,
        device=device,
        hub=hub,
        disable_update=disable_update,
        ncpu=ncpu,
        log_level=log_level,
        disable_pbar=disable_pbar,
    )


def fetch_model_choices(base_url: str, timeout: float) -> tuple[list[tuple[str, str]], str, dict]:
    """从后端读取模型列表，失败时返回静态兜底选项。"""
    try:
        payload = request_json(f"{base_url.rstrip('/')}/v1/models", timeout)
        choices = parse_model_choices(payload)
        if choices:
            return choices, summarize_model_status(payload), payload
        fallback_payload = {"data": [{"id": DEFAULT_MODEL, "ready": False}]}
        return (
            [(DEFAULT_MODEL, DEFAULT_MODEL)],
            "后端返回了空模型列表，已回退默认模型",
            fallback_payload,
        )
    except Exception as error:
        fallback_payload = {"data": [{"id": DEFAULT_MODEL, "ready": False}]}
        return (
            [(DEFAULT_MODEL, DEFAULT_MODEL)],
            f"模型列表加载失败，已回退默认模型：{error}",
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


def format_streaming_preview_text(full_text: str, final_flag: bool) -> str:
    """把 streaming 全量文本整理为预览文本，不主动换行或补标点。"""
    _ = final_flag
    return truncate_tail_text(str(full_text or "").strip(), STREAMING_PREVIEW_MAX_CHARS)


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
        gr.Button("预留执行入口", interactive=False)
        gr.Button("预留下载入口", interactive=False)


def refresh_model_dropdown(base_url: str, timeout: float):
    """刷新模型下拉框，并同步返回状态文本。"""
    try:
        import gradio as gr
    except ImportError as error:
        raise SystemExit("Install Gradio first: pip install gradio") from error

    choices, status_text, _ = fetch_model_choices(base_url, timeout)
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
    return (
        gr.update(choices=choices, value=choose_default_model(choices) or DEFAULT_MODEL),
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
    )


def initialize_service_dashboard(base_url: str, timeout: float, capability_filter: str):
    """页面加载时自动初始化服务页，减少手动点击刷新。"""
    try:
        import gradio as gr
    except ImportError as error:
        raise SystemExit("Install Gradio first: pip install gradio") from error

    normalized_base_url = base_url.rstrip("/")
    choices, status_text, models_payload = fetch_model_choices(normalized_base_url, timeout)
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
        gr.update(choices=choices, value=choose_default_model(choices) or DEFAULT_MODEL),
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
    default_model_value = choose_default_model(model_choices) or DEFAULT_MODEL
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

    with gr.Blocks(title="Pat-FunASR 语音识别") as demo:
        gr.Markdown("# Pat-FunASR WebUI")

        base_url = gr.Textbox(label="API 地址", value=default_base_url, visible=False)
        timeout = gr.Number(label="超时时间(秒)", value=default_timeout, precision=0, visible=False)
        service_tab_active = gr.State(False)

        with gr.Tabs():
            with gr.Tab("离线识别") as offline_tab:
                batch_response_format = gr.State(DEFAULT_BATCH_RESPONSE_FORMAT)
                with gr.Row():
                    media_file = gr.File(
                        label="音频/视频文件",
                        type="filepath",
                        file_types=list(MEDIA_FILE_SUFFIXES),
                    )
                    batch_files = gr.Files(
                        label="批量文件",
                        file_count="multiple",
                        type="filepath",
                        file_types=list(MEDIA_FILE_SUFFIXES),
                    )
                with gr.Row():
                    media_preview = gr.Video(
                        label="视频预览",
                        visible=False,
                        height=260,
                        elem_classes=["pat-media-preview"],
                    )
                    media_audio_preview = gr.Audio(label="音频预览", visible=False)
                    media_status = gr.Markdown("支持音频与视频文件。视频和音频都会显示可播放预览。")
                with gr.Row():
                    model = gr.Dropdown(
                        label="模型",
                        choices=model_choices,
                        value=default_model_value,
                    )
                with gr.Accordion("高级参数", open=False):
                    with gr.Row():
                        language = gr.Textbox(label="语言提示", placeholder="如：zh / en / auto")
                        hotword = gr.Textbox(label="热词", placeholder="多个热词可用逗号分隔")
                        vad_preset = gr.Dropdown(
                            label="VAD 预设",
                            choices=[("自动", ""), ("default", "default"), ("anti_hallucination", "anti_hallucination")],
                            value="",
                        )
                    with gr.Row():
                        merge_vad = gr.Dropdown(
                            label="合并 VAD 片段",
                            choices=[("自动", ""), ("启用", "true"), ("禁用", "false")],
                            value="",
                        )
                        use_itn = gr.Dropdown(
                            label="逆文本正规化",
                            choices=[("自动", ""), ("启用", "true"), ("禁用", "false")],
                            value="",
                        )
                        merge_length_s = gr.Number(label="合并段长度(秒)", value=15, precision=0)
                        max_line_width = gr.Number(label="字幕单行最大长度", value=40, precision=0)
                    with gr.Row():
                        batch_size_s = gr.Number(label="batch_size_s", value=0, precision=0)
                        vad_max_single_segment_time = gr.Number(
                            label="VAD 单段最大时长(ms)",
                            value=0,
                            precision=0,
                        )
                        punc_mode = gr.Dropdown(
                            label="PUNC 策略",
                            choices=[("自动", "auto"), ("关闭外置 PUNC", "disabled")],
                            value="auto",
                        )
                with gr.Accordion("运行时控制", open=False):
                    gr.Markdown("这些参数只影响当前请求，不额外拆页面。")
                    with gr.Row():
                        device = gr.Textbox(label="device", placeholder="如：cuda / cpu")
                        hub = gr.Dropdown(
                            label="hub",
                            choices=[("默认", ""), ("ModelScope", "ms"), ("HuggingFace", "hf")],
                            value="",
                        )
                        disable_update = gr.Dropdown(
                            label="disable_update",
                            choices=[("默认", ""), ("启用", "true"), ("禁用", "false")],
                            value="",
                        )
                    with gr.Row():
                        ncpu = gr.Number(label="ncpu", value=0, precision=0)
                        log_level = gr.Dropdown(
                            label="log_level",
                            choices=[("默认", ""), ("DEBUG", "DEBUG"), ("INFO", "INFO"), ("WARNING", "WARNING"), ("ERROR", "ERROR")],
                            value="",
                        )
                        disable_pbar = gr.Dropdown(
                            label="disable_pbar",
                            choices=[("默认", ""), ("启用", "true"), ("禁用", "false")],
                            value="true",
                        )
                with gr.Row():
                    transcribe_button = gr.Button("开始识别", variant="primary")
                    batch_button = gr.Button("批量执行")
                    retry_failed_button = gr.Button("重试失败项")

                transcript_payload_state = gr.State("{}")
                transcript_preview_format = gr.Radio(
                    label="预览格式",
                    choices=PREVIEW_FORMAT_CHOICES,
                    value=DEFAULT_PREVIEW_FORMAT,
                )
                transcript = gr.Textbox(label="结果预览", lines=12, max_lines=24, buttons=["copy"])
                with gr.Row():
                    download_json = gr.File(label="下载 JSON", visible=True)
                    download_txt = gr.File(label="下载 TXT", visible=True)
                    download_srt = gr.File(label="下载 SRT", visible=True)
                with gr.Row():
                    download_vtt = gr.File(label="下载 VTT", visible=True)
                    download_tsv = gr.File(label="下载 TSV", visible=True)
                    download_zip = gr.File(label="下载 ZIP", visible=True)
                batch_status = gr.Textbox(label="批量结果", lines=10, max_lines=20)
                batch_download = gr.File(label="批量下载结果", visible=True)
                failed_batch_state = gr.State([])

            with gr.Tab("流式识别") as streaming_tab:
                with gr.Row():
                    stream_media_file = gr.File(
                        label="音频/视频文件",
                        type="filepath",
                        file_types=list(MEDIA_FILE_SUFFIXES),
                    )
                    stream_model = gr.Dropdown(
                        label="Streaming 模型",
                        choices=streaming_model_choices,
                        value=default_streaming_model_value,
                    )
                with gr.Row():
                    stream_preview = gr.Video(
                        label="视频预览",
                        visible=False,
                        height=260,
                        elem_classes=["pat-media-preview"],
                    )
                    stream_audio_preview = gr.Audio(label="音频预览", visible=False)
                    stream_media_status = gr.Markdown("Streaming 仅显示支持流式的模型，当前默认使用 Paraformer Streaming。")
                with gr.Row():
                    stream_chunk_size = gr.Textbox(label="chunk_size", value="0,10,5")
                    stream_encoder_lb = gr.Number(label="encoder_chunk_look_back", value=0, precision=0)
                    stream_decoder_lb = gr.Number(label="decoder_chunk_look_back", value=0, precision=0)
                    stream_button = gr.Button("开始 Streaming", variant="secondary")
                stream_status = gr.Textbox(label="Streaming 状态", interactive=False)
                stream_transcript = gr.Textbox(label="Streaming 输出", lines=6, max_lines=20, buttons=["copy"])

            with gr.Tab("说话人分离") as diarization_tab:
                with gr.Row():
                    diarization_media_file = gr.File(
                        label="音频/视频文件",
                        type="filepath",
                        file_types=list(MEDIA_FILE_SUFFIXES),
                    )
                    diarization_model = gr.Dropdown(
                        label="识别模型",
                        choices=diarization_model_choices,
                        value=default_diarization_model_value,
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
                        label="spk_model",
                        choices=[("cam++", "cam++")],
                        value="cam++",
                    )
                    diarization_spk_mode = gr.Dropdown(
                        label="spk_mode",
                        choices=[
                            ("punc_segment", "punc_segment"),
                            ("vad_segment", "vad_segment"),
                            ("default", "default"),
                        ],
                        value="punc_segment",
                    )
                    diarization_preset_spk_num = gr.Number(label="preset_spk_num", value=0, precision=0)
                    diarization_button = gr.Button("开始说话人分离", variant="secondary")
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

            with gr.Tab("情感识别") as emotion_tab:
                with gr.Row():
                    emotion_media_file = gr.File(
                        label="音频/视频文件",
                        type="filepath",
                        file_types=list(MEDIA_FILE_SUFFIXES),
                    )
                    emotion_model = gr.Dropdown(
                        label="情感模型",
                        choices=emotion_model_choices,
                        value=default_emotion_model_value,
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
                        label="granularity",
                        choices=[("utterance", "utterance"), ("frame", "frame")],
                        value="utterance",
                    )
                    emotion_button = gr.Button("开始情感识别", variant="secondary")
                emotion_summary = gr.Textbox(label="情感结果", lines=4, max_lines=8)
                emotion_raw_json = gr.Textbox(label="情感原始 JSON", lines=10, max_lines=20)

            with gr.Tab("服务与调试") as service_tab:
                gr.Markdown("用于查看服务运行状态、模型加载方式、语言覆盖、能力分布、调试返回与运行日志。建议需要时再开启日志自动刷新。")
                with gr.Row():
                    gr.Markdown(f"- API：`{default_base_url}`\n- UI：默认 `7861/7862/7863` 自动择空闲端口")
                with gr.Row():
                    log_max_lines = gr.Slider(
                        label="日志行数",
                        minimum=50,
                        maximum=2000,
                        step=50,
                        value=120,
                    )
                    log_max_kb = gr.Slider(
                        label="单文件读取上限(KB)",
                        minimum=64,
                        maximum=2048,
                        step=64,
                        value=256,
                    )
                    log_max_section_chars = gr.Slider(
                        label="单段显示上限(字符)",
                        minimum=2000,
                        maximum=40000,
                        step=2000,
                        value=8000,
                    )
                    auto_refresh_logs = gr.Checkbox(label="自动刷新服务与调试(可能影响性能)", value=True)
                with gr.Row():
                    refresh_models_button = gr.Button("刷新模型列表")
                    check_button = gr.Button("检查服务")
                    refresh_logs_button = gr.Button("刷新运行日志")
                    download_logs_button = gr.Button("打包下载运行日志", variant="secondary")
                model_status = gr.Textbox(label="模型摘要", value=model_status_text, interactive=False)
                capability_filter = gr.Dropdown(
                    label="能力筛选",
                    choices=CAPABILITY_FILTER_CHOICES,
                    value="all",
                )
                service_overview = gr.Markdown(initial_overview_markdown)
                capability_target = gr.Markdown(initial_target_markdown)
                service_capability = gr.Markdown(initial_capability_markdown)
                service_raw_json = gr.Textbox(label="服务状态 / 调试输出", lines=10, max_lines=20)
                runtime_logs = gr.Textbox(
                    label="运行日志",
                    value=initial_runtime_logs,
                    lines=18,
                    max_lines=30,
                    interactive=False,
                )
                runtime_logs_archive = gr.File(label="日志下载", visible=True)
                runtime_log_timer = gr.Timer(value=5.0)

        check_button.click(
            fn=safe_check_with_capabilities,
            inputs=[base_url, timeout, capability_filter],
            outputs=[service_raw_json, service_overview, service_capability, capability_target],
        )
        capability_filter.change(
            fn=safe_render_capabilities,
            inputs=[base_url, timeout, capability_filter],
            outputs=[service_overview, service_capability, capability_target],
        )
        refresh_models_button.click(
            fn=refresh_model_dropdown,
            inputs=[base_url, timeout],
            outputs=[model, stream_model, emotion_model, diarization_model, model_status],
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
        offline_tab.select(
            fn=lambda: set_service_tab_auto_refresh_active(False),
            outputs=[service_tab_active],
        )
        streaming_tab.select(
            fn=lambda: set_service_tab_auto_refresh_active(False),
            outputs=[service_tab_active],
        )
        diarization_tab.select(
            fn=lambda: set_service_tab_auto_refresh_active(False),
            outputs=[service_tab_active],
        )
        emotion_tab.select(
            fn=lambda: set_service_tab_auto_refresh_active(False),
            outputs=[service_tab_active],
        )
        service_tab.select(
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
        media_file.change(
            fn=update_media_preview,
            inputs=[media_file],
            outputs=[media_preview, media_audio_preview, media_status],
        )
        stream_media_file.change(
            fn=update_media_preview,
            inputs=[stream_media_file],
            outputs=[stream_preview, stream_audio_preview, stream_media_status],
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

        stream_button.click(
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
