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
import urllib.error
import urllib.request
import uuid
import zipfile

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from app_utils import (
    DEFAULT_MODEL,
    build_request_fields,
    choose_default_model,
    initialize_batch_results,
    is_binary_response_format,
    normalize_uploaded_paths,
    output_filename_for_format,
    parse_model_choices,
    summarize_batch_results,
)

DEFAULT_BASE_URL = "http://localhost:8000"


def request_json(url: str, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
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
) -> tuple[str, str, str | None]:
    if not audio_path:
        return "", "上传或录制音频文件后再点击识别。", None

    base_url = base_url.rstrip("/")

    video_exts = {
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
    }
    path = Path(audio_path)
    if path.suffix.lower() in video_exts:
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
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw_bytes = response.read()
        if is_binary_response_format(response_format):
            suffix_name = output_filename_for_format(response_format)
            output_path = Path(tempfile.gettempdir()) / f"pat-funasr-{uuid.uuid4().hex}-{suffix_name}"
            output_path.write_bytes(raw_bytes)
            preview_text = raw_bytes.decode("utf-8", errors="replace")
            return preview_text, preview_text, str(output_path)
        payload = json.loads(raw_bytes.decode("utf-8"))

    text = payload.get("text", "")
    return text, json.dumps(payload, ensure_ascii=False, indent=2), None


def check_service(base_url: str, timeout: float) -> str:
    base_url = base_url.rstrip("/")
    health = request_json(f"{base_url}/health", timeout)
    models = request_json(f"{base_url}/v1/models", timeout)
    return json.dumps({"health": health, "models": models}, ensure_ascii=False, indent=2)


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
        import subprocess

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
        )
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        return "", f"HTTP {error.code} from {error.url}: {detail}", None
    except Exception as error:
        return "", f"Transcription failed: {error}", None


def safe_check(base_url: str, timeout: float) -> str:
    try:
        return check_service(base_url, timeout)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        return f"HTTP {error.code} from {error.url}: {detail}"
    except Exception as error:
        return f"Service check failed: {error}"


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
        )
        ok = bool(download_path) or (
            not raw_content.startswith("HTTP ") and not raw_content.startswith("Transcription failed:")
        )
        if ok:
            result_path = download_path or build_result_file_from_payload(response_format, raw_content)
            results[index]["status"] = "success"
            results[index]["ok"] = True
            results[index]["message"] = transcript or "ok"
            results[index]["result_path"] = result_path
        else:
            results[index]["status"] = "error"
            results[index]["ok"] = False
            results[index]["message"] = raw_content or transcript or "未知错误"
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
    )


def fetch_model_choices(base_url: str, timeout: float) -> tuple[list[tuple[str, str]], str]:
    """从后端读取模型列表，失败时返回静态兜底选项。"""
    try:
        payload = request_json(f"{base_url.rstrip('/')}/v1/models", timeout)
        choices = parse_model_choices(payload)
        if choices:
            return choices, f"已加载 {len(choices)} 个模型"
        return [(DEFAULT_MODEL, DEFAULT_MODEL)], "后端返回了空模型列表，已回退默认模型"
    except Exception as error:
        return [(DEFAULT_MODEL, DEFAULT_MODEL)], f"模型列表加载失败，已回退默认模型：{error}"


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


def refresh_model_dropdown(base_url: str, timeout: float):
    """刷新模型下拉框，并同步返回状态文本。"""
    try:
        import gradio as gr
    except ImportError as error:
        raise SystemExit("Install Gradio first: pip install gradio") from error

    choices, status_text = fetch_model_choices(base_url, timeout)
    return gr.update(choices=choices, value=choose_default_model(choices) or DEFAULT_MODEL), status_text


def build_app(default_base_url: str, default_timeout: float):
    try:
        import gradio as gr
    except ImportError as error:
        raise SystemExit("Install Gradio first: pip install gradio") from error

    model_choices, model_status_text = fetch_model_choices(default_base_url, default_timeout)
    default_model_value = choose_default_model(model_choices) or DEFAULT_MODEL

    with gr.Blocks(title="Pat-FunASR 语音识别") as demo:
        gr.Markdown("# Pat-FunASR 语音识别")
        gr.Markdown("上传或录制音频/视频文件，点击【开始识别】进行语音转文字。")

        with gr.Row():
            base_url = gr.Textbox(label="API 地址", value=default_base_url, visible=False)
            model = gr.Dropdown(
                label="模型",
                choices=model_choices,
                value=default_model_value,
            )
            response_format = gr.Radio(
                label="输出格式",
                choices=["json", "verbose_json", "txt", "srt", "vtt", "tsv", "all"],
                value="verbose_json",
            )
            timeout = gr.Number(label="超时时间(秒)", value=default_timeout, precision=0, visible=False)
        with gr.Row():
            refresh_models_button = gr.Button("刷新模型列表")
            model_status = gr.Textbox(label="模型状态", value=model_status_text, interactive=False)
        format_help = gr.Markdown(explain_response_format("verbose_json"))

        model_info = gr.HTML(
            """
        <div style="background:#f0f4ff;border-radius:8px;padding:12px 16px;margin:0 0 12px 0;max-width:680px;border:1px solid #d0d9f0">
        <b>SenseVoice 多语言</b> &nbsp;|&nbsp; 推荐 ⭐⭐⭐⭐⭐<br>
        支持 <b>中英日韩等20+语言</b>，含情感识别，速度快 · 精度高，适合大多数场景
        </div>
        """
        )

        video = gr.Video(label="音频/视频", sources=["upload", "webcam"], include_audio=True)
        batch_files = gr.Files(label="批量文件", file_count="multiple", type="filepath")
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
            check_button = gr.Button("检查服务")
            transcribe_button = gr.Button("开始识别", variant="primary")
            batch_button = gr.Button("批量执行")
            retry_failed_button = gr.Button("重试失败项")

        transcript = gr.Textbox(label="识别结果", lines=8, max_lines=20, buttons=["copy"])
        download_file = gr.File(label="下载结果", visible=True)
        batch_status = gr.Textbox(label="批量结果", lines=10, max_lines=20)
        batch_download = gr.File(label="批量下载结果", visible=True)
        failed_batch_state = gr.State([])
        with gr.Accordion("原始 JSON", open=False):
            raw_json = gr.Code(label="", language="json")

        check_button.click(fn=safe_check, inputs=[base_url, timeout], outputs=[raw_json])
        refresh_models_button.click(
            fn=refresh_model_dropdown,
            inputs=[base_url, timeout],
            outputs=[model, model_status],
        )
        response_format.change(
            fn=explain_response_format,
            inputs=[response_format],
            outputs=[format_help],
        )
        transcribe_button.click(
            fn=safe_transcribe,
            inputs=[
                base_url,
                video,
                model,
                response_format,
                timeout,
                language,
                hotword,
                vad_preset,
                merge_vad,
                use_itn,
                merge_length_s,
                max_line_width,
            ],
            outputs=[transcript, raw_json, download_file],
        )
        batch_button.click(
            fn=batch_transcribe,
            inputs=[
                batch_files,
                base_url,
                model,
                response_format,
                timeout,
                language,
                hotword,
                vad_preset,
                merge_vad,
                use_itn,
                merge_length_s,
                max_line_width,
            ],
            outputs=[batch_status, batch_download, failed_batch_state],
        )
        retry_failed_button.click(
            fn=retry_failed_batch,
            inputs=[
                failed_batch_state,
                base_url,
                model,
                response_format,
                timeout,
                language,
                hotword,
                vad_preset,
                merge_vad,
                use_itn,
                merge_length_s,
                max_line_width,
            ],
            outputs=[batch_status, batch_download, failed_batch_state],
        )

        MODEL_CARDS = {
            "sensevoice": (
                '<div style="background:#f0f4ff;border-radius:8px;padding:12px 16px;margin:0 0 12px 0;max-width:680px;border:1px solid #d0d9f0">'
                "<b>SenseVoice 多语言</b> &nbsp;|&nbsp; 推荐 ⭐⭐⭐⭐⭐<br>"
                "支持 <b>中英日韩等20+语言</b>，含情感识别，速度快 · 精度高，适合大多数场景"
                "</div>"
            ),
            "paraformer": (
                '<div style="background:#fff8e6;border-radius:8px;padding:12px 16px;margin:0 0 12px 0;max-width:680px;border:1px solid #f0d9a0">'
                "<b>Paraformer 中文</b> &nbsp;|&nbsp; 中文精度最高 ⭐⭐⭐⭐⭐<br>"
                "专为 <b>中文普通话</b> 优化的 Paraformer-large 模型，中文识别精度最高，适合中文播客/视频"
                "</div>"
            ),
            "paraformer-en": (
                '<div style="background:#fff0f0;border-radius:8px;padding:12px 16px;margin:0 0 12px 0;max-width:680px;border:2px solid #e06060">'
                "<b>⚠ Paraformer 英文</b> &nbsp;|&nbsp; 仅限英文音频 ⭐⭐⭐⭐<br>"
                "<b>仅支持英文</b>！用于中文/其他语言会报错。如需中文请选 SenseVoice 或 Paraformer 中文"
                "</div>"
            ),
            "fun-asr-nano": (
                '<div style="background:#fff0f0;border-radius:8px;padding:12px 16px;margin:0 0 12px 0;max-width:680px;border:1px solid #d9a0a0">'
                "<b>Fun-ASR-Nano</b> &nbsp;|&nbsp; 轻量LLM ⭐⭐⭐<br>"
                "基于 Qwen3-0.6B 大模型，支持 <b>多语言</b>，推理较慢（CPU），适合对语言细节有高要求的场景"
                "</div>"
            ),
        }

        model.change(fn=lambda m: MODEL_CARDS.get(m, ""), inputs=[model], outputs=[model_info])

    return demo


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Gradio demo for the FunASR OpenAI-compatible API")
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", DEFAULT_BASE_URL), help="FunASR API base URL")
    parser.add_argument("--host", default=os.getenv("GRADIO_HOST", "127.0.0.1"), help="Gradio bind host")
    parser.add_argument("--port", type=int, default=int(os.getenv("GRADIO_PORT", "7861")), help="Gradio bind port")
    parser.add_argument("--timeout", type=float, default=float(os.getenv("TIMEOUT", "300")), help="HTTP timeout in seconds")
    parser.add_argument("--share", action="store_true", help="Create a temporary Gradio share link")
    args = parser.parse_args()

    app = build_app(args.base_url, args.timeout)
    app.launch(server_name=args.host, server_port=args.port, share=args.share)


if __name__ == "__main__":
    main()
