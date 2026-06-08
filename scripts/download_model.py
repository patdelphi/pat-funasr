# -*- coding: utf-8 -*-
"""
FunASR-Portable-GPU 模型下载脚本

用途：
- 下载 SenseVoiceSmall（iic/SenseVoiceSmall）到本便携包的 workspace/models 缓存目录
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    root_dir = Path(__file__).resolve().parents[1]
    cache_dir = root_dir / "workspace" / "models"

    os.environ.setdefault("MODELSCOPE_CACHE", str(cache_dir))

    model_id = "iic/SenseVoiceSmall"
    try:
        from modelscope import snapshot_download
    except Exception as exc:
        print(f"[FAIL] modelscope import failed: {exc}", file=sys.stderr)
        return 1

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        local_dir = snapshot_download(model_id, cache_dir=str(cache_dir))
        print(f"[PASS] Downloaded: {model_id}")
        print(f"       Path: {local_dir}")
        return 0
    except Exception as exc:
        print(f"[FAIL] Download failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

