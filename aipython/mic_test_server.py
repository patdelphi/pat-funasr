"""
程序说明：启动独立浏览器麦克风诊断页，只测试真实 Mic 收声，不连接 FunASR 推理链路。
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
import webbrowser


def build_mic_test_html() -> str:
    """返回独立 Mic 诊断页面 HTML。"""
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>真实 Mic 收声测试</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #18202a;
      --muted: #657182;
      --line: #d9dee7;
      --accent: #0b6bcb;
      --danger: #c0362c;
      --ok: #1b7f4b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
    }
    main {
      max-width: 1100px;
      margin: 0 auto;
      padding: 28px;
    }
    h1 {
      margin: 0 0 18px;
      font-size: 26px;
      font-weight: 700;
      letter-spacing: 0;
    }
    .layout {
      display: grid;
      grid-template-columns: 360px minmax(0, 1fr);
      gap: 18px;
      align-items: start;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }
    label {
      display: block;
      margin: 0 0 8px;
      color: var(--muted);
      font-size: 13px;
    }
    select, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: white;
      color: var(--text);
      font: inherit;
      padding: 10px;
    }
    select { min-height: 42px; }
    textarea {
      min-height: 180px;
      resize: vertical;
      font-family: Consolas, "Microsoft YaHei", monospace;
      font-size: 13px;
      line-height: 1.5;
    }
    .row {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 14px;
    }
    button, a.download {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: white;
      color: var(--text);
      cursor: pointer;
      font: inherit;
      min-height: 40px;
      padding: 8px 13px;
      text-decoration: none;
    }
    button.primary { background: var(--accent); border-color: var(--accent); color: white; }
    button.stop { background: var(--danger); border-color: var(--danger); color: white; }
    button:disabled, a[aria-disabled="true"] { opacity: .45; cursor: not-allowed; pointer-events: none; }
    .meter {
      height: 18px;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #eef1f5;
      margin: 10px 0;
    }
    .meter > div {
      width: 0%;
      height: 100%;
      background: var(--ok);
      transition: width .08s linear;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin: 14px 0;
    }
    .stat {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fbfcfd;
    }
    .stat strong {
      display: block;
      font-size: 20px;
      margin-top: 4px;
    }
    canvas {
      width: 100%;
      height: 260px;
      display: block;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #101820;
    }
    .status {
      color: var(--muted);
      margin: 0 0 12px;
      min-height: 22px;
    }
    @media (max-width: 860px) {
      main { padding: 16px; }
      .layout { grid-template-columns: 1fr; }
      .stats { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <h1>真实 Mic 收声测试</h1>
    <div class="layout">
      <section>
        <label for="deviceSelect">输入设备</label>
        <select id="deviceSelect"></select>
        <div class="row">
          <button id="refreshButton">刷新设备</button>
          <button id="startButton" class="primary">开始测试</button>
          <button id="stopButton" class="stop" disabled>停止测试</button>
        </div>
        <div class="row">
          <a id="downloadLink" class="download" aria-disabled="true">下载录音</a>
        </div>
      </section>
      <section>
        <p id="status" class="status">未开始。请先选择设备并点击开始测试。</p>
        <div class="meter"><div id="levelBar"></div></div>
        <div class="stats">
          <div class="stat">峰值<strong id="peakValue">0.000</strong></div>
          <div class="stat">RMS<strong id="rmsValue">0.000</strong></div>
          <div class="stat">采样率<strong id="sampleRateValue">-</strong></div>
        </div>
        <canvas id="waveCanvas" width="900" height="260"></canvas>
        <label for="logBox" style="margin-top:14px;">诊断日志</label>
        <textarea id="logBox" readonly></textarea>
      </section>
    </div>
  </main>
  <script>
    const deviceSelect = document.getElementById("deviceSelect");
    const refreshButton = document.getElementById("refreshButton");
    const startButton = document.getElementById("startButton");
    const stopButton = document.getElementById("stopButton");
    const downloadLink = document.getElementById("downloadLink");
    const statusEl = document.getElementById("status");
    const levelBar = document.getElementById("levelBar");
    const peakValue = document.getElementById("peakValue");
    const rmsValue = document.getElementById("rmsValue");
    const sampleRateValue = document.getElementById("sampleRateValue");
    const logBox = document.getElementById("logBox");
    const canvas = document.getElementById("waveCanvas");
    const canvasContext = canvas.getContext("2d");

    let stream = null;
    let audioContext = null;
    let analyser = null;
    let sourceNode = null;
    let animationId = null;
    let mediaRecorder = null;
    let recordedChunks = [];
    let lastObjectUrl = "";

    function log(message) {
      const time = new Date().toLocaleTimeString();
      logBox.value += `[${time}] ${message}\\n`;
      logBox.scrollTop = logBox.scrollHeight;
    }

    function setStatus(message) {
      statusEl.textContent = message;
      log(message);
    }

    function resetMeter() {
      levelBar.style.width = "0%";
      peakValue.textContent = "0.000";
      rmsValue.textContent = "0.000";
      canvasContext.clearRect(0, 0, canvas.width, canvas.height);
    }

    async function refreshDevices() {
      if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
        setStatus("当前浏览器不支持 mediaDevices.enumerateDevices。");
        return;
      }
      const devices = await navigator.mediaDevices.enumerateDevices();
      const inputs = devices.filter((device) => device.kind === "audioinput");
      deviceSelect.innerHTML = "";
      const defaultOption = document.createElement("option");
      defaultOption.value = "";
      defaultOption.textContent = "系统默认输入设备";
      deviceSelect.appendChild(defaultOption);
      inputs.forEach((device, index) => {
        const option = document.createElement("option");
        option.value = device.deviceId;
        option.textContent = device.label || `麦克风 ${index + 1}`;
        deviceSelect.appendChild(option);
      });
      setStatus(`已发现 ${inputs.length} 个音频输入设备。`);
    }

    function drawWaveform(data) {
      canvasContext.fillStyle = "#101820";
      canvasContext.fillRect(0, 0, canvas.width, canvas.height);
      canvasContext.lineWidth = 2;
      canvasContext.strokeStyle = "#36c28a";
      canvasContext.beginPath();
      const sliceWidth = canvas.width / data.length;
      let x = 0;
      for (let i = 0; i < data.length; i += 1) {
        const y = (data[i] / 255) * canvas.height;
        if (i === 0) {
          canvasContext.moveTo(x, y);
        } else {
          canvasContext.lineTo(x, y);
        }
        x += sliceWidth;
      }
      canvasContext.lineTo(canvas.width, canvas.height / 2);
      canvasContext.stroke();
    }

    function tick() {
      if (!analyser) {
        return;
      }
      const timeData = new Uint8Array(analyser.fftSize);
      analyser.getByteTimeDomainData(timeData);
      let peak = 0;
      let sumSquares = 0;
      for (let i = 0; i < timeData.length; i += 1) {
        const centered = (timeData[i] - 128) / 128;
        const abs = Math.abs(centered);
        peak = Math.max(peak, abs);
        sumSquares += centered * centered;
      }
      const rms = Math.sqrt(sumSquares / timeData.length);
      levelBar.style.width = `${Math.min(100, Math.round(peak * 100))}%`;
      peakValue.textContent = peak.toFixed(3);
      rmsValue.textContent = rms.toFixed(3);
      drawWaveform(timeData);
      animationId = requestAnimationFrame(tick);
    }

    async function startTest() {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        setStatus("当前浏览器不支持 navigator.mediaDevices.getUserMedia。");
        return;
      }
      await stopTest(false);
      const selectedDeviceId = deviceSelect.value;
      const constraints = {
        audio: selectedDeviceId
          ? { deviceId: { exact: selectedDeviceId }, echoCancellation: false, noiseSuppression: false, autoGainControl: false }
          : { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
        video: false,
      };
      setStatus("正在请求麦克风权限...");
      try {
        stream = await navigator.mediaDevices.getUserMedia(constraints);
        audioContext = new AudioContext();
        analyser = audioContext.createAnalyser();
        analyser.fftSize = 2048;
        sourceNode = audioContext.createMediaStreamSource(stream);
        sourceNode.connect(analyser);
        sampleRateValue.textContent = `${audioContext.sampleRate} Hz`;
        recordedChunks = [];
        mediaRecorder = new MediaRecorder(stream);
        mediaRecorder.ondataavailable = (event) => {
          if (event.data && event.data.size > 0) {
            recordedChunks.push(event.data);
          }
        };
        mediaRecorder.onstop = () => {
          if (lastObjectUrl) {
            URL.revokeObjectURL(lastObjectUrl);
          }
          const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType || "audio/webm" });
          lastObjectUrl = URL.createObjectURL(blob);
          downloadLink.href = lastObjectUrl;
          downloadLink.download = `mic-test-${Date.now()}.webm`;
          downloadLink.setAttribute("aria-disabled", "false");
        };
        mediaRecorder.start(500);
        startButton.disabled = true;
        stopButton.disabled = false;
        downloadLink.setAttribute("aria-disabled", "true");
        setStatus("正在收声。说话时峰值、RMS 和波形应同步变化。");
        animationId = requestAnimationFrame(tick);
        await refreshDevices();
      } catch (error) {
        setStatus(`麦克风启动失败：${error.name || "Error"} ${error.message || error}`);
        await stopTest(false);
      }
    }

    async function stopTest(showMessage = true) {
      if (animationId) {
        cancelAnimationFrame(animationId);
        animationId = null;
      }
      if (mediaRecorder && mediaRecorder.state !== "inactive") {
        mediaRecorder.stop();
      }
      if (sourceNode) {
        sourceNode.disconnect();
        sourceNode = null;
      }
      if (audioContext) {
        await audioContext.close();
        audioContext = null;
      }
      if (stream) {
        stream.getTracks().forEach((track) => track.stop());
        stream = null;
      }
      analyser = null;
      startButton.disabled = false;
      stopButton.disabled = true;
      sampleRateValue.textContent = "-";
      resetMeter();
      if (showMessage) {
        setStatus("已停止测试。可下载刚才录到的音频检查是否有声音。");
      }
    }

    refreshButton.addEventListener("click", () => refreshDevices().catch((error) => setStatus(`刷新设备失败：${error.message || error}`)));
    startButton.addEventListener("click", () => startTest());
    stopButton.addEventListener("click", () => stopTest(true));
    window.addEventListener("beforeunload", () => {
      if (stream) {
        stream.getTracks().forEach((track) => track.stop());
      }
    });
    refreshDevices().catch((error) => setStatus(`初始化设备列表失败：${error.message || error}`));
  </script>
</body>
</html>
"""


class MicTestHandler(BaseHTTPRequestHandler):
    """只服务 Mic 诊断页和健康检查。"""

    def do_GET(self) -> None:
        """处理 HTTP GET 请求。"""
        if self.path in {"/", "/mic-test"}:
            body = build_mic_test_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/health":
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404, "Not Found")

    def log_message(self, format: str, *args) -> None:
        """减少控制台噪音，只保留启动信息。"""
        return


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="Pat-FunASR Mic diagnostic page")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7870)
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """启动独立 Mic 诊断 HTTP 服务。"""
    args = parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), MicTestHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Mic test page: {url}", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Mic test server stopped.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
