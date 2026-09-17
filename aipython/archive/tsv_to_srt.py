# -*- coding: utf-8 -*-
"""TSV 转 SRT 格式转换"""
import sys
from pathlib import Path

def seconds_to_srt_time(seconds: float) -> str:
    """将秒数转为 SRT 时间格式 HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds % 1) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def tsv_to_srt(tsv_path: str) -> str:
    content = Path(tsv_path).read_text(encoding="utf-8-sig").lstrip("\ufeff")
    lines = content.strip().splitlines()
    srt_blocks = []
    for i, line in enumerate(lines, 1):
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        start = float(parts[0])
        end = float(parts[1])
        text = "\t".join(parts[2:])
        srt_blocks.append(
            f"{i}\n{seconds_to_srt_time(start)} --> {seconds_to_srt_time(end)}\n{text}"
        )
    return "\n\n".join(srt_blocks) + "\n"

if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else r"Y:\NewStore\AI\pat-funasr\test\1.tsv"
    dst = Path(src).with_suffix(".srt")
    result = tsv_to_srt(src)
    dst.write_text(result, encoding="utf-8-sig")
    print(f"OK: {dst} ({len(result.splitlines())} lines)")
