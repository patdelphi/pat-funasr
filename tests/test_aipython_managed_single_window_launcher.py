"""
程序说明：
测试 "aipython/managed_single_window_launcher.py" 的关键启动辅助逻辑（unittest）。

目标：
- 校验端口选择逻辑按顺序挑空闲端口。
- 校验 API/UI 子进程命令构造正确。
- 校验启动环境变量会注入单窗口模式和 UI 端口。
"""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _ROOT / "aipython" / "managed_single_window_launcher.py"
_SPEC = importlib.util.spec_from_file_location("funasr_managed_single_window_launcher", _SCRIPT_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"无法加载脚本：{_SCRIPT_PATH}")
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


class TestManagedSingleWindowLauncher(unittest.TestCase):
    def test_ensure_required_ports_available_rejects_busy_api_port(self):
        with self.assertRaisesRegex(RuntimeError, "8000"):
            mod.ensure_required_ports_available(7861, is_port_free=lambda port: port != 8000)

    def test_pick_first_free_port_prefers_first_available(self):
        free_port = mod.pick_first_free_port([7861, 7862, 7863], is_port_free=lambda port: port != 7861)
        self.assertEqual(free_port, 7862)

    def test_pick_first_free_port_returns_none_when_all_busy(self):
        free_port = mod.pick_first_free_port([7861, 7862], is_port_free=lambda _port: False)
        self.assertIsNone(free_port)

    def test_build_cmd_call_wraps_bat_and_args(self):
        script_path = Path(r"Y:\NewStore\AI\FunASR-Portable-GPU\run_api.bat")
        command = mod.build_cmd_call(script_path, ["cuda"])
        self.assertEqual(command[1:4], ["/d", "/c", "call"])
        self.assertEqual(command[4], str(script_path))
        self.assertEqual(command[5:], ["cuda"])

    def test_build_child_env_sets_single_window_and_ui_port(self):
        base_env = {"PATH": "demo"}
        env = mod.build_child_env(base_env, ui_port=7862)
        self.assertEqual(env["PATH"], "demo")
        self.assertEqual(env["FUNASR_SINGLE_WINDOW"], "1")
        self.assertEqual(env["FUNASR_UI_PORT"], "7862")

    def test_write_log_header_contains_title_and_root(self):
        log_path = _ROOT / "tests" / "managed-single-window-log-header.tmp"
        try:
            mod.write_log_header(log_path, "Pat WebUI", _ROOT)
            text = log_path.read_text(encoding="utf-8")
        finally:
            if log_path.exists():
                log_path.unlink()
        self.assertIn("Pat WebUI", text)
        self.assertIn(f"root={_ROOT}", text)

    def test_write_log_header_appends_instead_of_truncating(self):
        log_path = _ROOT / "tests" / "managed-single-window-log-append.tmp"
        try:
            log_path.write_text("existing-line\n", encoding="utf-8")
            mod.write_log_header(log_path, "Pat WebUI", _ROOT)
            text = log_path.read_text(encoding="utf-8")
        finally:
            if log_path.exists():
                log_path.unlink()
        self.assertIn("existing-line", text)
        self.assertIn("Pat WebUI", text)

    def test_log_tail_reader_starts_from_end_by_default(self):
        fd, temp_name = tempfile.mkstemp(prefix="pat-log-tail-", suffix=".log")
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            temp_path.write_text("old-line-1\nold-line-2\n", encoding="utf-8")
            reader = mod.LogTailReader(temp_path, "T")
            self.assertEqual(reader._position, temp_path.stat().st_size)
            self.assertEqual(reader.drain(), False)
        finally:
            if temp_path.exists():
                temp_path.unlink()


if __name__ == "__main__":
    unittest.main()
