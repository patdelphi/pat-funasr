# -*- coding: utf-8 -*-
"""Browser demo for the FunASR OpenAI-compatible API."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.request
import uuid

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_MODEL = "sensevoice"


def request_json(url: str, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def multipart_body(audio_path: Path, model: str, response_format: str) -> tuple[bytes, str]:
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
    add_text("model", model)
    add_text("response_format", response_format)
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), boundary


def transcribe_audio(
    base_url: str, audio_path: str | None, model: str, response_format: str, timeout: float
) -> tuple[str, str]:
    if not audio_path:
        return "", "上传或录制音频文件后再点击识别。"

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
            return "", "不支持的视频格式，仅支持音频文件(wav/mp3/m4a/flac/ogg)"

    body, boundary = multipart_body(path, model, response_format)
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
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    text = payload.get("text", "")
    return text, json.dumps(payload, ensure_ascii=False, indent=2)


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
    base_url: str, audio_path: str | None, model: str, response_format: str, timeout: float
) -> tuple[str, str]:
    try:
        return transcribe_audio(base_url, audio_path, model, response_format, timeout)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        return "", f"HTTP {error.code} from {error.url}: {detail}"
    except Exception as error:
        return "", f"Transcription failed: {error}"


def safe_check(base_url: str, timeout: float) -> str:
    try:
        return check_service(base_url, timeout)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        return f"HTTP {error.code} from {error.url}: {detail}"
    except Exception as error:
        return f"Service check failed: {error}"


def build_app(default_base_url: str, default_timeout: float):
    try:
        import gradio as gr
    except ImportError as error:
        raise SystemExit("Install Gradio first: pip install gradio") from error

    with gr.Blocks(title="Pat-FunASR 语音识别") as demo:
        gr.Markdown("# Pat-FunASR 语音识别")
        gr.Markdown("上传或录制音频/视频文件，点击【开始识别】进行语音转文字。")

        with gr.Row():
            base_url = gr.Textbox(label="API 地址", value=default_base_url, visible=False)
            model = gr.Dropdown(
                label="模型",
                choices=[
                    ("SenseVoice 多语言", "sensevoice"),
                    ("Paraformer 中文", "paraformer"),
                    ("Paraformer 英文", "paraformer-en"),
                    ("Fun-ASR-Nano", "fun-asr-nano"),
                ],
                value=DEFAULT_MODEL,
            )
            response_format = gr.Radio(label="输出格式", choices=["json", "verbose_json"], value="verbose_json")
            timeout = gr.Number(label="超时时间(秒)", value=default_timeout, precision=0, visible=False)

        model_info = gr.HTML(
            """
        <div style="background:#f0f4ff;border-radius:8px;padding:12px 16px;margin:0 0 12px 0;max-width:680px;border:1px solid #d0d9f0">
        <b>SenseVoice 多语言</b> &nbsp;|&nbsp; 推荐 ⭐⭐⭐⭐⭐<br>
        支持 <b>中英日韩等20+语言</b>，含情感识别，速度快 · 精度高，适合大多数场景
        </div>
        """
        )

        video = gr.Video(label="音频/视频", sources=["upload", "webcam"], include_audio=True)
        with gr.Row():
            check_button = gr.Button("检查服务")
            transcribe_button = gr.Button("开始识别", variant="primary")

        transcript = gr.Textbox(label="识别结果", lines=8, max_lines=20, buttons=["copy"])
        with gr.Accordion("原始 JSON", open=False):
            raw_json = gr.Code(label="", language="json")

        check_button.click(fn=safe_check, inputs=[base_url, timeout], outputs=[raw_json])
        transcribe_button.click(
            fn=safe_transcribe,
            inputs=[base_url, video, model, response_format, timeout],
            outputs=[transcript, raw_json],
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

