# -*- coding: utf-8 -*-
"""Python launcher for FunASR (GPU edition) - auto-detects CUDA and falls back to CPU."""
import os, sys, subprocess, time, webbrowser

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.path.join(ROOT_DIR, "runtime", "python", "python.exe")
API_DIR = os.path.join(ROOT_DIR, "app", "openai_api")
MODELS = os.path.join(ROOT_DIR, "workspace", "models")
PYTHONPATH = os.path.join(ROOT_DIR, "runtime", "python") + os.pathsep + \
             os.path.join(ROOT_DIR, "runtime", "python", "Lib", "site-packages") + os.pathsep + \
             os.path.join(ROOT_DIR, "app")

env = os.environ.copy()
env["MODELSCOPE_CACHE"] = MODELS
env["HF_HOME"] = os.path.join(MODELS, "huggingface")
env["PYTHONPATH"] = PYTHONPATH

print("=" * 50)
print("  FunASR Speech Recognition (GPU)")
print("=" * 50)
print()
print(f"API dir:  {API_DIR}")
print(f"Python:   {PYTHON}")
print(f"Models:   {MODELS}")
print()

# --- device detection --------------------------------------------------
device = "cpu"
try:
    sys.path.insert(0, PYTHONPATH)
    import torch  # noqa: E402
    if torch.cuda.is_available():
        device = "cuda"
        gpu_name = torch.cuda.get_device_name(0)
        gpu_mem = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        print(f"[GPU]  {gpu_name} ({gpu_mem:.1f} GB)")
        print(f"[CUDA] torch {torch.version.cuda}, device cap {torch.cuda.get_device_capability(0)}")
    else:
        print("[WARN] No CUDA GPU detected.")
        print("       Falling back to CPU. This build is tuned for GPU -")
        print("       expect ~10x slower inference than the GPU build.")
except Exception as e:
    print(f"[WARN] torch import failed: {e}")
    print("       Falling back to CPU.")
print()

# Start API server
print(f"[1/2] Starting API Server on http://localhost:8000 (device={device}) ...")
api_proc = subprocess.Popen(
    [PYTHON, "-X", "utf8", "server.py", "--model", "sensevoice", "--device", device, "--port", "8000"],
    cwd=API_DIR, env=env
)
print(f"      PID: {api_proc.pid}")
print()

# Wait for API to start
print("[WAIT] Waiting for API to start...")
for i in range(20):
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:8000/health", timeout=2)
        print(f"[PASS] API ready after ~{i+1}s")
        break
    except Exception:
        time.sleep(1)
else:
    print("[WARN] API health check timed out, continuing anyway...")

print()

# Start Gradio UI
print("[2/2] Starting Gradio UI on http://localhost:7860 ...")
ui_proc = subprocess.Popen(
    [PYTHON, "-X", "utf8", "gradio_app.py", "--base-url", "http://localhost:8000", "--port", "7860"],
    cwd=API_DIR, env=env
)
print(f"      PID: {ui_proc.pid}")

time.sleep(2)
try:
    webbrowser.open("http://localhost:7860")
except Exception:
    pass

print()
print("=" * 50)
print("  FunASR GPU Started!")
print("=" * 50)
print()
print(f"API:    http://localhost:8000")
print(f"UI:     http://localhost:7860")
print(f"Device: {device}")
print()
print("Press Ctrl+C to stop all services.")
print()

try:
    api_proc.wait()
    ui_proc.wait()
except KeyboardInterrupt:
    print("\n[STOP] Stopping services...")
    api_proc.terminate()
    ui_proc.terminate()
    api_proc.wait()
    ui_proc.wait()
    print("[STOP] Done.")
