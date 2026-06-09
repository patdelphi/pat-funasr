"""
程序说明：
单窗口托管启动 "FunASR API" 与 "Pat WebUI"。

目标：
- 保持只弹出一个启动窗口。
- API/UI 日志落盘，并在当前窗口实时输出。
- 关闭当前启动窗口时，自动结束 API/UI 子进程。
"""

from __future__ import annotations

import argparse
import ctypes
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_EXE = ROOT_DIR / "runtime" / "python" / "python.exe"
API_BAT = ROOT_DIR / "run_api.bat"
UI_BAT = ROOT_DIR / "run_ui_pat.bat"
API_LOG = ROOT_DIR / "funasr-api.log"
UI_LOG = ROOT_DIR / "funasr-ui.log"
DEBUG_LOG = ROOT_DIR / "trae-debug-log-bat-startup-failure.txt"
DEFAULT_UI_PORTS = [7861, 7862, 7863]

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def log_debug(message: str) -> None:
    """把启动链路调试信息写入根目录日志文件。"""
    with DEBUG_LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{message}\n")


def write_log_header(log_path: Path, title: str, root_dir: Path) -> None:
    """重置日志文件，并写入当前启动批次头信息。"""
    header_text = "\n".join(
        [
            "",
            "==================================================",
            title,
            f"started at {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"root={root_dir}",
            "==================================================",
            "",
        ]
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(header_text)


def is_port_free(port: int) -> bool:
    """判断本机端口当前是否空闲。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", int(port)))
        except OSError:
            return False
    return True


def pick_first_free_port(ports: list[int], is_port_free=is_port_free) -> int | None:
    """按顺序挑选第一个空闲端口。"""
    for port in ports:
        if is_port_free(int(port)):
            return int(port)
    return None


def ensure_required_ports_available(ui_port: int, is_port_free=is_port_free) -> None:
    """校验 API 和目标 UI 端口是否都空闲。"""
    if not is_port_free(8000):
        raise RuntimeError('required API port "8000" is already in use')
    if not is_port_free(int(ui_port)):
        raise RuntimeError(f'required UI port "{ui_port}" is already in use')


def parse_port_candidates(raw: str) -> list[int]:
    """解析 UI 端口候选列表。"""
    values = []
    for item in str(raw or "").split(","):
        stripped = item.strip()
        if not stripped:
            continue
        values.append(int(stripped))
    return values or DEFAULT_UI_PORTS


def build_cmd_call(script_path: Path, args: list[str] | None = None) -> list[str]:
    """构造通过 cmd 调用 bat 的命令。"""
    comspec = os.environ.get("ComSpec", "cmd.exe")
    command = [comspec, "/d", "/c", "call", str(script_path)]
    if args:
        command.extend(args)
    return command


def build_child_env(base_env: dict[str, str], ui_port: int) -> dict[str, str]:
    """为 API/UI 子进程补充单窗口相关环境变量。"""
    env = dict(base_env)
    env["FUNASR_SINGLE_WINDOW"] = "1"
    env["FUNASR_UI_PORT"] = str(int(ui_port))
    return env


def create_kill_on_close_job():
    """创建 kill-on-close Job Object，确保宿主进程退出时子进程一起结束。"""
    if os.name != "nt":
        return None
    kernel32 = ctypes.windll.kernel32
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError("CreateJobObjectW failed")

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    result = kernel32.SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not result:
        kernel32.CloseHandle(job)
        raise OSError("SetInformationJobObject failed")
    return job


def assign_process_to_job(job_handle, process: subprocess.Popen) -> None:
    """把子进程加入 Job Object。"""
    if job_handle is None:
        return
    kernel32 = ctypes.windll.kernel32
    result = kernel32.AssignProcessToJobObject(job_handle, ctypes.c_void_p(process._handle))
    if not result:
        raise OSError(f"AssignProcessToJobObject failed for PID {process.pid}")


def start_child_process(
    *,
    command: list[str],
    env: dict[str, str],
    log_path: Path,
    cwd: Path,
    job_handle,
) -> subprocess.Popen:
    """启动隐藏子进程，并重定向输出到日志文件。"""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with log_path.open("a", encoding="utf-8", buffering=1) as log_handle:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    assign_process_to_job(job_handle, process)
    return process


class LogTailReader:
    """按增量读取日志文件，并输出新增内容。"""

    def __init__(self, log_path: Path, prefix: str, *, start_from_end: bool = True):
        self.log_path = log_path
        self.prefix = prefix
        self._position = 0
        if start_from_end and self.log_path.exists():
            try:
                self._position = self.log_path.stat().st_size
            except OSError:
                self._position = 0

    def drain(self) -> bool:
        """输出新增日志行。"""
        if not self.log_path.exists():
            return False
        with self.log_path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(self._position)
            lines = handle.readlines()
            self._position = handle.tell()
        for line in lines:
            text = line.rstrip("\n")
            if not text:
                print("")
                continue
            if "\r" in text:
                segment = text.split("\r")[-1]
                sys.stdout.write(f"\r[{self.prefix}] {segment}")
                sys.stdout.flush()
                continue
            text = text.rstrip("\r")
            print(f"[{self.prefix}] {text}")
        return bool(lines)


def print_launch_banner(ui_port: int, device: str) -> None:
    """输出启动摘要。"""
    print(f'[1/2] starting API: "run_api.bat" (DEVICE={device})')
    print(f'[2/2] starting UI: "run_ui_pat.bat" (port {ui_port})')
    print(f"launched: API=8000, Pat WebUI={ui_port}")
    print(f'API log: "{API_LOG}"')
    print(f'UI log: "{UI_LOG}"')
    print(f"open browser after WebUI is ready: http://127.0.0.1:{ui_port}")
    print()
    print("==================== Live Logs ====================")


def wait_and_tail_logs(api_proc: subprocess.Popen, ui_proc: subprocess.Popen) -> int:
    """持续打印日志，直到 API/UI 退出。"""
    readers = [
        LogTailReader(API_LOG, "API"),
        LogTailReader(UI_LOG, "UI"),
    ]
    last_exit_code = 0
    while True:
        had_output = False
        for reader in readers:
            had_output = reader.drain() or had_output

        api_code = api_proc.poll()
        ui_code = ui_proc.poll()
        if api_code is not None and ui_code is not None:
            for reader in readers:
                reader.drain()
            last_exit_code = api_code or ui_code or 0
            break
        if not had_output:
            time.sleep(0.5)
    return int(last_exit_code)


def stop_process(process: subprocess.Popen | None) -> None:
    """尝试优雅结束子进程，失败时强制结束。"""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def ensure_required_files() -> None:
    """检查启动所需文件是否存在。"""
    for path in (PYTHON_EXE, API_BAT, UI_BAT):
        if not path.exists():
            raise FileNotFoundError(f"missing required file: {path}")


def main() -> int:
    """启动入口。"""
    parser = argparse.ArgumentParser(description="Pat WebUI single-window managed launcher")
    parser.add_argument("--device", default="cuda", help="API device, e.g. cuda/cpu")
    parser.add_argument("--ui-ports", default="7861,7862,7863", help="candidate UI ports")
    args = parser.parse_args()

    ensure_required_files()
    device = str(args.device or "cuda")
    ui_port = pick_first_free_port(parse_port_candidates(args.ui_ports))
    if ui_port is None:
        print("ERROR: no free UI port found in 7861,7862,7863", file=sys.stderr)
        return 1
    try:
        ensure_required_ports_available(ui_port)
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    write_log_header(API_LOG, "FunASR API log", ROOT_DIR)
    write_log_header(UI_LOG, "FunASR UI log", ROOT_DIR)
    log_debug(f"==== START {time.strftime('%Y-%m-%d %H:%M:%S')} ====")
    log_debug(f"ROOT={ROOT_DIR}")
    log_debug(f"DEVICE={device}")
    log_debug(f"UI_PORT={ui_port}")

    env = build_child_env(os.environ.copy(), ui_port=ui_port)
    job_handle = create_kill_on_close_job()
    api_proc = None
    ui_proc = None
    try:
        print_launch_banner(ui_port=ui_port, device=device)
        api_proc = start_child_process(
            command=build_cmd_call(API_BAT, [device]),
            env=env,
            log_path=API_LOG,
            cwd=ROOT_DIR,
            job_handle=job_handle,
        )
        ui_proc = start_child_process(
            command=build_cmd_call(UI_BAT),
            env=env,
            log_path=UI_LOG,
            cwd=ROOT_DIR,
            job_handle=job_handle,
        )
        return wait_and_tail_logs(api_proc, ui_proc)
    except KeyboardInterrupt:
        print("\n收到中断信号，正在停止 API/UI ...")
        return 0
    finally:
        stop_process(ui_proc)
        stop_process(api_proc)
        if job_handle is not None:
            ctypes.windll.kernel32.CloseHandle(job_handle)


if __name__ == "__main__":
    raise SystemExit(main())
