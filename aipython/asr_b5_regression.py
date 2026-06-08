"""
程序说明：
B5 回归验证脚本（本地）。

用途：
- 对本地 FunASR OpenAI-Compatible API（/v1/audio/transcriptions）做单文件多格式回归。
- 覆盖 response_format：json / verbose_json / txt / srt / vtt / tsv / all(zip)。

约束：
- 仅访问本机 base_url（默认 http://127.0.0.1:8000），不访问外网。
- 所有请求带异常处理，失败会打印错误并继续跑剩余格式。
"""

from __future__ import annotations

import argparse
import mimetypes
from pathlib import Path
import sys
import urllib.error
import urllib.request
import uuid


def parse_formats(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def multipart_body(audio_path: Path, fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = f"----funasr-b5-{uuid.uuid4().hex}"
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
    for key, value in fields.items():
        add_text(key, value)
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), boundary


def post_transcriptions(
    *,
    base_url: str,
    audio_path: Path,
    model: str,
    response_format: str,
    timeout: float,
) -> bytes:
    body, boundary = multipart_body(
        audio_path,
        {
            "model": model,
            "response_format": response_format,
        },
    )
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/audio/transcriptions",
        data=body,
        method="POST",
        headers={
            "Accept": "application/octet-stream",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser(description="B5 regression for FunASR OpenAI-Compatible API")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="API base URL")
    parser.add_argument("--audio", required=True, help="本地音频文件路径")
    parser.add_argument("--model", default="sensevoice", help="模型别名")
    parser.add_argument("--timeout", type=float, default=300.0, help="请求超时(秒)")
    parser.add_argument("--out-dir", default="b5_outputs", help="输出目录")
    parser.add_argument(
        "--formats",
        default="json,verbose_json,txt,srt,vtt,tsv,all",
        help="逗号分隔的 response_format 列表",
    )
    args = parser.parse_args()

    audio_path = Path(args.audio)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not audio_path.exists():
        print(f"音频文件不存在：{audio_path}", file=sys.stderr)
        return 2

    formats = parse_formats(args.formats)
    if not formats:
        print("formats 为空", file=sys.stderr)
        return 2
    ok_count = 0
    for fmt in formats:
        try:
            raw = post_transcriptions(
                base_url=args.base_url,
                audio_path=audio_path,
                model=args.model,
                response_format=fmt,
                timeout=args.timeout,
            )
            suffix = "zip" if fmt == "all" else fmt
            output_path = out_dir / f"{audio_path.stem}.{suffix}"
            output_path.write_bytes(raw)
            print(f"[OK] response_format={fmt} -> {output_path}")
            ok_count += 1
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            print(f"[ERR] response_format={fmt} HTTP {error.code} {error.url}: {detail}", file=sys.stderr)
        except Exception as error:
            print(f"[ERR] response_format={fmt} {error}", file=sys.stderr)

    if ok_count != len(formats):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
