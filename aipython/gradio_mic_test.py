"""
程序说明：启动独立 Gradio 麦克风流式诊断页，用于判断 Gradio Audio 是否把 Mic 音频块送到 Python。
"""

from __future__ import annotations

import argparse
import time
from typing import Any

import gradio as gr
import numpy as np


def describe_gradio_audio(audio: Any) -> tuple[str, float, float, int]:
    """解析 Gradio Audio 输入，返回诊断文本、峰值、RMS 与样本数。"""
    if audio is None:
        return "Python 未收到 Gradio 音频块。", 0.0, 0.0, 0
    if not isinstance(audio, tuple) or len(audio) != 2:
        return f"Python 收到非 numpy 音频类型：{type(audio).__name__}", 0.0, 0.0, 0

    sample_rate, data = audio
    array = np.asarray(data)
    if array.size == 0:
        return f"Python 收到空音频块；采样率：{sample_rate}Hz。", 0.0, 0.0, 0
    if array.ndim > 1:
        array = array.mean(axis=1)

    source_is_float = array.dtype.kind == "f"
    values = array.astype(np.float32, copy=False)
    if not source_is_float:
        values = values / 32768.0
    peak = float(np.max(np.abs(values)))
    rms = float(np.sqrt(np.mean(values * values)))
    status = (
        f"Python 已收到 Gradio 音频块；采样率：{int(sample_rate)}Hz；"
        f"样本数：{array.size}；dtype：{array.dtype}；峰值：{peak:.4f}；RMS：{rms:.4f}。"
    )
    return status, peak, rms, int(array.size)


def start_state() -> tuple[dict, str]:
    """初始化录音诊断状态。"""
    return {"count": 0, "started_at": time.time(), "last_status": ""}, "Gradio 录音已开始，等待 Python 收到音频块。"


def stream_audio(audio: Any, state: dict | None) -> tuple[dict, str, str]:
    """处理 Gradio stream 音频块。"""
    next_state = dict(state or {})
    next_state["count"] = int(next_state.get("count", 0) or 0) + 1
    status, peak, rms, samples = describe_gradio_audio(audio)
    next_state["last_status"] = status
    log = (
        f"stream #{next_state['count']} | peak={peak:.4f} | rms={rms:.4f} | "
        f"samples={samples} | {status}"
    )
    return next_state, status, log


def stop_state(state: dict | None) -> str:
    """停止录音后汇总诊断状态。"""
    current = dict(state or {})
    count = int(current.get("count", 0) or 0)
    if count <= 0:
        return "Gradio 录音已停止；Python 没收到任何 stream 音频块。"
    return f"Gradio 录音已停止；Python 共收到 {count} 个 stream 音频块。最后状态：{current.get('last_status', '')}"


def build_demo() -> gr.Blocks:
    """构建独立 Gradio Mic 诊断页面。"""
    with gr.Blocks(title="Gradio Mic 流式诊断") as demo:
        gr.Markdown("# Gradio Mic 流式诊断")
        mic = gr.Audio(
            label="Gradio 麦克风",
            sources=["microphone"],
            type="numpy",
            streaming=True,
            recording=False,
        )
        status = gr.Textbox(label="Python 收声状态", interactive=False)
        log = gr.Textbox(label="最近一个 stream 事件", interactive=False, lines=4)
        state = gr.State({})

        mic.start_recording(fn=start_state, outputs=[state, status])
        stream_event = mic.stream(
            fn=stream_audio,
            inputs=[mic, state],
            outputs=[state, status, log],
            show_progress="hidden",
            trigger_mode="multiple",
            stream_every=0.25,
        )
        mic.stop_recording(fn=stop_state, inputs=[state], outputs=[status], cancels=[stream_event])
    return demo


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="Gradio Mic streaming diagnostic page")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7871)
    return parser.parse_args()


def main() -> int:
    """启动 Gradio Mic 诊断页。"""
    args = parse_args()
    demo = build_demo()
    demo.queue().launch(server_name=args.host, server_port=args.port, inbrowser=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
