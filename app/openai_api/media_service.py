"""
程序说明：
FunASR API 共享媒体上传服务。

职责：
- 按固定大小分块读取上传文件，避免整文件先进入内存。
- 对普通媒体和流式 PCM 分片执行统一的字节上限。
- 超限或写入失败时删除不完整临时文件。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


DEFAULT_READ_CHUNK_BYTES = 1024 * 1024


class UploadTooLargeError(ValueError):
    """上传内容超过服务端限制。"""

    def __init__(self, *, actual_bytes: int, limit_bytes: int):
        self.actual_bytes = int(actual_bytes)
        self.limit_bytes = int(limit_bytes)
        super().__init__(
            f"Upload too large: {self.actual_bytes} bytes exceeds limit of "
            f"{self.limit_bytes} bytes"
        )


def _safe_suffix(filename: str | None, default_suffix: str) -> str:
    """只保留短扩展名，避免文件名进入临时路径。"""
    suffix = Path(str(filename or "")).suffix.lower()
    if not suffix or len(suffix) > 12 or not re.fullmatch(r"\.[a-z0-9]+", suffix):
        suffix = default_suffix
    if not suffix.startswith("."):
        suffix = f".{suffix}"
    return suffix


def _validate_limits(max_bytes: int, chunk_bytes: int) -> tuple[int, int]:
    max_bytes = int(max_bytes)
    chunk_bytes = int(chunk_bytes)
    if max_bytes <= 0:
        raise ValueError("max_bytes must be > 0")
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be > 0")
    return max_bytes, chunk_bytes


async def save_upload_file(
    upload: Any,
    temp_dir: str | Path,
    *,
    max_bytes: int,
    chunk_bytes: int = DEFAULT_READ_CHUNK_BYTES,
    default_suffix: str = ".wav",
) -> tuple[str, int]:
    """将上传内容分块写入指定临时目录，返回路径和总字节数。"""
    max_bytes, chunk_bytes = _validate_limits(max_bytes, chunk_bytes)
    destination_dir = Path(temp_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    suffix = _safe_suffix(getattr(upload, "filename", None), default_suffix)
    destination = destination_dir / f"upload{suffix}"
    total = 0

    try:
        with destination.open("wb") as output:
            while True:
                chunk = await upload.read(chunk_bytes)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UploadTooLargeError(
                        actual_bytes=total,
                        limit_bytes=max_bytes,
                    )
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    return str(destination), total


async def read_upload_bytes_limited(
    upload: Any,
    *,
    max_bytes: int,
    chunk_bytes: int = DEFAULT_READ_CHUNK_BYTES,
) -> bytes:
    """分块读取需要保留在内存中的小型上传内容。"""
    max_bytes, chunk_bytes = _validate_limits(max_bytes, chunk_bytes)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(chunk_bytes)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLargeError(actual_bytes=total, limit_bytes=max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)
