# -*- coding: utf-8 -*-
"""P0: FunASR Portable GPU 冒烟测试"""
import subprocess, time, urllib.request, json, os

ROOT = os.path.dirname(os.path.abspath(__file__))
BAT = os.path.join(ROOT, "FunASR.bat")
TEST_WAV = os.path.join(ROOT, "workspace", "models", "iic", "SenseVoiceSmall", "example", "zh.mp3")


def health():
    r = urllib.request.urlopen("http://localhost:8000/health", timeout=5)
    return json.loads(r.read())


def transcribe(model):
    boundary = "----p0-test"
    body = (f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"file\"; filename=\"test.mp3\"\r\n"
            f"Content-Type: audio/mpeg\r\n\r\n").encode("utf-8")
    body += open(TEST_WAV, "rb").read()
    body += f"\r\n--{boundary}\r\n".encode("utf-8")
    body += f"Content-Disposition: form-data; name=\"model\"\r\n\r\n{model}\r\n".encode("utf-8")
    body += f"--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:8000/v1/audio/transcriptions",
        data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read()).get("text", "")


if __name__ == "__main__":
    print("=== P0: FunASR Portable GPU 冒烟测试 ===")
    print()

    # 1. 启动 start_services.py（直接在当前进程派生，避免 BAT 套娃把子进程甩掉）
    print("[1] 启动 start_services.py (GPU)...")
    py = os.path.join(ROOT, "runtime", "python", "python.exe")
    torch_lib = os.path.join(ROOT, "runtime", "python", "Lib", "site-packages", "torch", "lib")
    env = os.environ.copy()
    env["PATH"] = torch_lib + os.pathsep + env.get("PATH", "")
    services_proc = subprocess.Popen(
        [py, "start_services.py"],
        cwd=ROOT, env=env,
    )
    time.sleep(75)  # GPU 启动：CUDA 初始化 + SenseVoice 模型加载到 GPU

    # 2. 健康检查
    print("[2] API 健康检查...", end=" ")
    h = health()
    assert h["status"] == "ok", f"API 不健康: {h}"
    print("PASS")

    # 3. Gradio UI
    print("[3] Gradio UI 可访问...", end=" ")
    r = urllib.request.urlopen("http://localhost:7860", timeout=5)
    assert r.status == 200, "Gradio 不可访问"
    print("PASS")

    # 4. 模型列表
    print("[4] 模型注册...", end=" ")
    models = h["models_available"]
    assert "sensevoice" in models, "SenseVoice 未注册"
    print(f"PASS ({len(models)} 个: {', '.join(models)})")

    # 5. 转写测试
    print("[5] 转写测试 (跳过 paraformer-en)...")
    for model in ["sensevoice", "paraformer"]:
        print(f"    {model}...", end=" ")
        text = transcribe(model)
        assert len(text) > 0, f"{model} 转写结果为空"
        print(f"PASS ({len(text)} 字)")

    # 6. 清理
    print("[6] 清理进程...")
    try:
        services_proc.terminate()
        services_proc.wait(timeout=10)
    except Exception:
        pass
    subprocess.run(["taskkill", "/F", "/IM", "python.exe"], capture_output=True, shell=True)

    print()
    print("=== P0 测试全部通过 ===")
