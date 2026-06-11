"""
程序说明：启动独立 Gradio Mic 流式识别测试页。

本页面只用于验证 Gradio Audio.stream 是否能完成：
浏览器 Mic -> Gradio Python 回调 -> Pat-FunASR streaming API -> 实时文本显示。
"""

from __future__ import annotations

import argparse
import http.client
import json
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import gradio as gr
import numpy as np


DEFAULT_API_BASE = "http://127.0.0.1:8000"
DEFAULT_MODEL = "paraformer-zh-streaming"
DEFAULT_CHUNK_SIZE = "0,30,15"


DEVICE_PICKER_HTML = """
<div class="pat-device-panel" style="border:1px solid var(--border-color-primary,#e5e7eb);border-radius:8px;padding:12px;margin:8px 0;">
  <label for="patMicDeviceSelect" style="display:block;font-weight:600;margin-bottom:6px;">输入设备</label>
  <select id="patMicDeviceSelect" style="width:100%;min-height:38px;border-radius:6px;padding:6px;">
    <option value="">系统默认输入设备</option>
  </select>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;">
    <button type="button" id="patRefreshMicDevices">刷新设备</button>
    <button type="button" id="patProbeMicPermission">请求麦克风权限</button>
  </div>
  <p id="patMicDeviceStatus" style="margin:8px 0 0;color:var(--body-text-color-subdued,#6b7280);font-size:13px;">
    未枚举设备。
  </p>
</div>
"""


DEVICE_PICKER_JS = """
(function setupPatMicDevicePicker() {
  if (window.__patMicDevicePickerInstalled) return;
  window.__patMicDevicePickerInstalled = true;
  window.__patSelectedMicDeviceId = "";

  function findElement(id) {
    return document.getElementById(id);
  }

  function setStatus(message) {
    const status = findElement("patMicDeviceStatus");
    if (status) status.textContent = message;
  }

  function resetOptions() {
    const select = findElement("patMicDeviceSelect");
    if (!select) return null;
    const previous = select.value;
    select.innerHTML = "";
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "系统默认输入设备";
    select.appendChild(option);
    return previous;
  }

  async function refreshDevices() {
    const select = findElement("patMicDeviceSelect");
    if (!select) return;
    const previous = resetOptions();
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
      setStatus("当前浏览器不支持 enumerateDevices。");
      return;
    }
    const devices = await navigator.mediaDevices.enumerateDevices();
    const inputs = devices.filter((device) => device.kind === "audioinput");
    inputs.forEach((device, index) => {
      const option = document.createElement("option");
      option.value = device.deviceId;
      option.textContent = device.label || `麦克风 ${index + 1}`;
      select.appendChild(option);
    });
    if ([...select.options].some((option) => option.value === previous)) {
      select.value = previous;
    } else {
      const defaultInput = inputs.find((device) => device.deviceId === "default");
      select.value = defaultInput ? defaultInput.deviceId : "";
    }
    window.__patSelectedMicDeviceId = select.value;
    const current = select.selectedOptions[0]?.textContent || "系统默认输入设备";
    setStatus(`发现 ${inputs.length} 个输入设备；Gradio 麦克风将使用：${current}`);
  }

  async function requestPermission() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus("当前浏览器不支持 getUserMedia。");
      return;
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    stream.getTracks().forEach((track) => track.stop());
    await refreshDevices();
  }

  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia && !navigator.mediaDevices.__patOriginalGetUserMedia) {
    navigator.mediaDevices.__patOriginalGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = function patchedGetUserMedia(constraints) {
      const deviceId = window.__patSelectedMicDeviceId || "";
      if (deviceId && constraints && constraints.audio) {
        const audio = typeof constraints.audio === "object" ? { ...constraints.audio } : {};
        audio.deviceId = { exact: deviceId };
        constraints = { ...constraints, audio };
      }
      return navigator.mediaDevices.__patOriginalGetUserMedia(constraints);
    };
  }

  function bindWhenReady() {
    const select = findElement("patMicDeviceSelect");
    const refresh = findElement("patRefreshMicDevices");
    const probe = findElement("patProbeMicPermission");
    if (!select || !refresh || !probe) {
      setTimeout(bindWhenReady, 200);
      return;
    }
    select.onchange = () => {
      window.__patSelectedMicDeviceId = select.value;
      const current = select.selectedOptions[0]?.textContent || "系统默认输入设备";
      setStatus(`已选择：${current}。下一次启动 Gradio 麦克风时生效。`);
    };
    refresh.onclick = () => refreshDevices().catch((error) => setStatus(`刷新设备失败：${error.message}`));
    probe.onclick = () => requestPermission().catch((error) => setStatus(`请求权限失败：${error.message}`));
    refreshDevices().catch((error) => setStatus(`初始化设备失败：${error.message}`));
  }

  bindWhenReady();
})();
"""


def normalize_audio(audio: Any) -> tuple[int, np.ndarray]:
    """把 Gradio Audio 的 numpy 输入转为单声道 float32。"""
    if audio is None:
        raise ValueError("还没有收到 Gradio 音频块")
    if not isinstance(audio, tuple) or len(audio) != 2:
        raise ValueError(f"收到不支持的音频类型：{type(audio).__name__}")

    sample_rate, data = audio
    array = np.asarray(data)
    if array.size == 0:
        raise ValueError("收到空音频块")
    source_is_float = array.dtype.kind == "f"
    values = array.astype(np.float32, copy=False)
    if not source_is_float:
        values = values / 32768.0
    if values.ndim > 1:
        values = values.mean(axis=1)
    return int(sample_rate), values


def resample_to_16k(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """用轻量线性插值把 Gradio 音频块转成 16k。"""
    if sample_rate == 16000:
        return samples.astype(np.float32, copy=False)
    if sample_rate <= 0:
        raise ValueError(f"采样率无效：{sample_rate}")

    duration = samples.shape[0] / float(sample_rate)
    out_len = max(1, int(round(duration * 16000)))
    src_x = np.linspace(0.0, duration, num=samples.shape[0], endpoint=False)
    dst_x = np.linspace(0.0, duration, num=out_len, endpoint=False)
    return np.interp(dst_x, src_x, samples).astype(np.float32)


def float_to_pcm16(samples: np.ndarray) -> bytes:
    """把 float32 [-1, 1] 音频转成 PCM16 little-endian。"""
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = np.where(clipped < 0, clipped * 32768.0, clipped * 32767.0).astype("<i2")
    return pcm.tobytes()


def http_json(
    method: str,
    api_base: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> dict:
    """调用本地 Pat-FunASR API 并解析 JSON。"""
    parsed = urlparse(api_base.rstrip("/"))
    if parsed.scheme != "http":
        raise ValueError("测试页仅支持 http API 地址")
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        text = resp.read().decode("utf-8", errors="replace")
    finally:
        conn.close()
    if resp.status >= 400:
        raise RuntimeError(f"{resp.status} {resp.reason}: {text}")
    return json.loads(text) if text else {}


def multipart_streaming_body(fields: dict[str, str], pcm: bytes) -> tuple[bytes, str]:
    """构造 multipart/form-data 请求体。"""
    boundary = "----patgradio" + uuid.uuid4().hex
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(b'Content-Disposition: form-data; name="file"; filename="chunk.pcm"\r\n')
    body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
    body.extend(pcm)
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), boundary


def preload_model(api_base: str, model: str) -> tuple[Any, str]:
    """加载 streaming 模型，加载完成后启用 Gradio Mic。"""
    status = http_json("GET", api_base, f"/v1/models/{model}/status")
    if not status.get("ready"):
        status = http_json("POST", api_base, f"/v1/models/{model}/load", timeout=300.0)
    if not status.get("ready"):
        return gr.update(interactive=False), f"模型未就绪：{status}"
    return gr.update(interactive=True), f"模型 {model} 已就绪，可以点击麦克风录制。"


def start_session(api_base: str, model: str, chunk_size: str) -> tuple[dict, str, str]:
    """初始化一次 Gradio streaming 识别会话。"""
    state = {
        "api_base": api_base.rstrip("/"),
        "model": model,
        "chunk_size": chunk_size,
        "session_id": uuid.uuid4().hex,
        "sent": 0,
        "full_text": "",
        "started_at": time.time(),
    }
    return state, "", "录音已开始，等待 Gradio stream 音频块。"


def stream_to_funasr(audio: Any, state: dict | None) -> tuple[dict, str, str]:
    """把 Gradio stream 音频块发送到 FunASR streaming API。"""
    current = dict(state or {})
    if not current:
        current, _, _ = start_session(DEFAULT_API_BASE, DEFAULT_MODEL, DEFAULT_CHUNK_SIZE)

    sample_rate, samples = normalize_audio(audio)
    down = resample_to_16k(samples, sample_rate)
    pcm = float_to_pcm16(down)

    fields = {
        "model": current.get("model", DEFAULT_MODEL),
        "session_id": current.get("session_id") or uuid.uuid4().hex,
        "reset": "true" if int(current.get("sent", 0) or 0) == 0 else "false",
        "is_final": "false",
        "chunk_size": current.get("chunk_size", DEFAULT_CHUNK_SIZE),
        "encoder_chunk_look_back": "4",
        "decoder_chunk_look_back": "1",
    }
    body, boundary = multipart_streaming_body(fields, pcm)
    payload = http_json(
        "POST",
        current.get("api_base", DEFAULT_API_BASE),
        "/v1/funasr/streaming",
        body=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    current["sent"] = int(current.get("sent", 0) or 0) + 1
    current["full_text"] = payload.get("full_text", current.get("full_text", ""))
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    rms = float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
    log = (
        f"分片 {current['sent']} | sr={sample_rate} -> 16000 | "
        f"samples={samples.size} | peak={peak:.4f} | rms={rms:.4f} | "
        f"text={payload.get('text', '')}"
    )
    return current, current["full_text"], log


def stop_session(state: dict | None) -> tuple[dict, str]:
    """停止录音后返回汇总状态。"""
    current = dict(state or {})
    sent = int(current.get("sent", 0) or 0)
    return current, f"已停止；共发送 {sent} 个 Gradio stream 分片。"


def build_demo(api_base: str, model: str, chunk_size: str) -> gr.Blocks:
    """构建独立 Gradio streaming ASR 测试页面。"""
    with gr.Blocks(title="Gradio Mic FunASR 流式识别测试") as demo:
        gr.Markdown("# Gradio Mic FunASR 流式识别测试")
        with gr.Row():
            with gr.Column(scale=1):
                api_input = gr.Textbox(label="API 地址", value=api_base)
                model_input = gr.Textbox(label="模型", value=model)
                chunk_input = gr.Textbox(label="chunk_size", value=chunk_size)
                load_button = gr.Button("加载模型")
                gr.HTML(DEVICE_PICKER_HTML, js_on_load=DEVICE_PICKER_JS)
                mic = gr.Audio(
                    label="Gradio 麦克风",
                    sources=["microphone"],
                    type="numpy",
                    streaming=True,
                    recording=False,
                    interactive=False,
                )
            with gr.Column(scale=1):
                status = gr.Textbox(label="状态", interactive=False)
                transcript = gr.Textbox(label="识别结果", interactive=False, lines=10)
                log = gr.Textbox(label="最近一个分片", interactive=False, lines=5)
        state = gr.State({})

        load_button.click(preload_model, inputs=[api_input, model_input], outputs=[mic, status])
        mic.start_recording(start_session, inputs=[api_input, model_input, chunk_input], outputs=[state, transcript, status])
        stream_event = mic.stream(
            stream_to_funasr,
            inputs=[mic, state],
            outputs=[state, transcript, log],
            show_progress="hidden",
            trigger_mode="multiple",
            stream_every=0.5,
        )
        mic.stop_recording(stop_session, inputs=[state], outputs=[state, status], cancels=[stream_event])
    return demo


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="Gradio Mic FunASR streaming ASR test page")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7872)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--chunk-size", default=DEFAULT_CHUNK_SIZE)
    return parser.parse_args()


def main() -> int:
    """启动 Gradio streaming ASR 测试页。"""
    args = parse_args()
    demo = build_demo(args.api_base, args.model, args.chunk_size)
    demo.queue().launch(server_name=args.host, server_port=args.port, inbrowser=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
