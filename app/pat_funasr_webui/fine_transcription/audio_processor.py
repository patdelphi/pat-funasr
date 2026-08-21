# -*- coding: utf-8 -*-
"""
程序说明：
音频前处理模块 — 对输入音频做降噪、重采样、VAD 静音裁剪、音量归一化。
全部基于 ffmpeg subprocess 实现，不引入额外 Python 依赖。

功能清单：
1. 降噪：afftdn（频域自适应降噪）
2. 重采样/位深：指定采样率 + 16-bit s16 + 单声道
3. VAD 静音裁剪：silenceremove 移除 >2s 的静音段
4. 音量归一化：loudnorm EBU R128 响度归一化
"""

import os
import re
import subprocess
import tempfile
import logging

logger = logging.getLogger("audio_processor")

# ffmpeg / ffprobe 可执行路径（优先环境变量，回退默认安装路径）
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", r"C:\ffmpeg\bin\ffmpeg.exe")
FFPROBE_BIN = os.environ.get("FFPROBE_BIN", r"C:\ffmpeg\bin\ffprobe.exe")


def get_audio_info(input_path: str) -> dict:
    """
    用 ffprobe 获取音频文件信息。

    参数:
        input_path: 音频文件路径

    返回:
        dict: {duration, sample_rate, channels, codec, bit_rate, size_mb}
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    cmd = [
        FFPROBE_BIN, "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        input_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    if result.returncode != 0:
        # ffprobe 失败时返回最小信息
        return {"error": result.stderr[-200:] if result.stderr else "unknown"}

    import json
    data = json.loads(result.stdout)

    # 提取音频流信息
    streams = data.get("streams", [])
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

    # 文件级信息
    fmt = data.get("format", {})

    info = {
        "duration": float(fmt.get("duration", 0)),
        "sample_rate": int(audio_stream.get("sample_rate", 0)),
        "channels": int(audio_stream.get("channels", 0)),
        "codec": audio_stream.get("codec_name", "unknown"),
        "bit_rate": int(fmt.get("bit_rate", 0)),
        "size_mb": round(os.path.getsize(input_path) / 1024 / 1024, 2),
    }
    return info


def process_audio(
    input_path: str,
    noise_reduction: bool = True,
    noise_strength: float = 12.0,
    sample_rate: int = 16000,
    vad_enabled: bool = False,
    loudnorm: bool = True,
    output_path: str = None,
) -> tuple:
    """
    音频前处理主函数。

    参数:
        input_path: 输入音频文件路径
        noise_reduction: 是否启用降噪（afftdn）
        noise_strength: 降噪强度(dB)，默认 12，范围 0-48
        sample_rate: 目标采样率，默认 16000
        vad_enabled: 是否启用 VAD 静音裁剪
        loudnorm: 是否启用音量归一化（loudnorm）
        output_path: 输出路径，None 则自动生成临时文件

    返回:
        (output_path, info_before, info_after) 三元组
        - output_path: 处理后 WAV 文件路径
        - info_before: 处理前音频信息 dict
        - info_after: 处理后音频信息 dict
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    if not os.path.exists(FFMPEG_BIN):
        raise FileNotFoundError(
            f"ffmpeg 未找到: {FFMPEG_BIN}，请设置 FFMPEG_BIN 环境变量"
        )

    # 获取处理前信息
    info_before = get_audio_info(input_path)

    if output_path is None:
        # 生成临时输出文件
        fd, output_path = tempfile.mkstemp(suffix=".wav", prefix="processed_")
        os.close(fd)

    # 构建 ffmpeg 滤镜链（顺序: 降噪 → VAD → 音量归一化）
    filters = []

    # 1. 降噪: afftdn 频域自适应降噪
    if noise_reduction:
        # nr=降噪强度, nf=噪声底抑制(负值增强)
        filters.append(f"afftdn=nr={noise_strength}:nf=-25")

    # 2. VAD 静音裁剪: silenceremove 移除 >2s 的静音段
    if vad_enabled:
        # stop_periods=-1 表示移除所有静音段
        # stop_duration=2: 静音 >2s 才裁剪
        # stop_threshold=-30dB: 静音判定阈值
        filters.append(
            "silenceremove=stop_periods=-1:stop_duration=2:stop_threshold=-30dB"
        )

    # 3. 音量归一化: loudnorm EBU R128
    if loudnorm:
        # I=目标响度, TP=真峰值上限, LRA=响度范围
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")

    filter_chain = ",".join(filters) if filters else None

    # 构建 ffmpeg 命令
    cmd = [FFMPEG_BIN, "-y", "-i", input_path]
    if filter_chain:
        cmd.extend(["-af", filter_chain])

    # 重采样 + 单声道 + 16-bit + WAV 格式
    cmd.extend([
        "-ar", str(sample_rate),
        "-ac", "1",
        "-sample_fmt", "s16",
        "-f", "wav",
        output_path,
    ])

    logger.info("执行 ffmpeg: %s", " ".join(cmd))

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 处理失败: {result.stderr[-500:]}")

    if not os.path.exists(output_path):
        raise RuntimeError("ffmpeg 输出文件不存在")

    # 获取处理后信息
    info_after = get_audio_info(output_path)

    logger.info(
        "音频前处理完成: %s (%.1fs → %.1fs)",
        output_path,
        info_before.get("duration", 0),
        info_after.get("duration", 0),
    )

    return output_path, info_before, info_after


def format_audio_info(info: dict) -> str:
    """
    将音频信息 dict 格式化为可读字符串。
    """
    if "error" in info:
        return f"❌ 获取失败: {info['error']}"

    duration = info.get("duration", 0)
    mins = int(duration // 60)
    secs = duration % 60

    lines = [
        f"时长: {mins}分{secs:.1f}秒 ({duration:.2f}s)",
        f"采样率: {info.get('sample_rate', 'N/A')} Hz",
        f"声道数: {info.get('channels', 'N/A')}",
        f"编码: {info.get('codec', 'N/A')}",
        f"码率: {info.get('bit_rate', 0) // 1000} kbps" if info.get("bit_rate") else "码率: N/A",
        f"文件大小: {info.get('size_mb', 'N/A')} MB",
    ]
    return "\n".join(lines)
