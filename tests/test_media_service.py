"""
程序说明：
共享媒体上传服务的单元测试。

目标：
- 验证上传内容按固定大小分块读取，不先把整文件读入内存。
- 验证超限时立即报错并删除不完整文件。
- 验证流式 PCM 分片使用独立的小尺寸上限。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_OPENAI_API_DIR = _ROOT / "app" / "openai_api"
if str(_OPENAI_API_DIR) not in sys.path:
    sys.path.insert(0, str(_OPENAI_API_DIR))

from media_service import (  # noqa: E402
    UploadTooLargeError,
    read_upload_bytes_limited,
    save_upload_file,
)


class _FakeUpload:
    def __init__(self, data: bytes, filename: str = "demo.wav"):
        self.data = data
        self.filename = filename
        self.offset = 0
        self.read_sizes = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if self.offset >= len(self.data):
            return b""
        if size < 0:
            size = len(self.data) - self.offset
        chunk = self.data[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class TestMediaService(unittest.IsolatedAsyncioTestCase):
    async def test_save_upload_file_reads_in_chunks(self):
        upload = _FakeUpload(b"abcdefghij", filename="sample.mp3")
        with tempfile.TemporaryDirectory() as tmpdir:
            path, total = await save_upload_file(
                upload,
                tmpdir,
                max_bytes=32,
                chunk_bytes=4,
            )
            self.assertEqual(Path(path).read_bytes(), b"abcdefghij")
            self.assertEqual(Path(path).suffix, ".mp3")

        self.assertEqual(total, 10)
        self.assertEqual(upload.read_sizes, [4, 4, 4, 4])
        self.assertNotIn(-1, upload.read_sizes)

    async def test_save_upload_file_removes_partial_file_when_too_large(self):
        upload = _FakeUpload(b"0123456789", filename="sample.wav")
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(UploadTooLargeError) as context:
                await save_upload_file(
                    upload,
                    tmpdir,
                    max_bytes=6,
                    chunk_bytes=4,
                )
            self.assertEqual(list(Path(tmpdir).iterdir()), [])

        self.assertEqual(context.exception.limit_bytes, 6)
        self.assertGreater(context.exception.actual_bytes, 6)

    async def test_read_upload_bytes_limited_rejects_large_stream_chunk(self):
        upload = _FakeUpload(b"0123456789", filename="chunk.pcm")
        with self.assertRaises(UploadTooLargeError):
            await read_upload_bytes_limited(upload, max_bytes=6, chunk_bytes=4)
        self.assertNotIn(-1, upload.read_sizes)


if __name__ == "__main__":
    unittest.main()
