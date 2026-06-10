"""
程序说明：验证独立麦克风诊断页是否包含真实收声所需的浏览器端诊断能力。
"""

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT_DIR / "aipython" / "mic_test_server.py"
spec = importlib.util.spec_from_file_location("mic_test_server", MODULE_PATH)
mic_test_server = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mic_test_server
spec.loader.exec_module(mic_test_server)


class TestMicTestServer(unittest.TestCase):
    def test_mic_test_page_contains_browser_capture_diagnostics(self):
        html = mic_test_server.build_mic_test_html()

        self.assertIn("navigator.mediaDevices.getUserMedia", html)
        self.assertIn("navigator.mediaDevices.enumerateDevices", html)
        self.assertIn("AudioContext", html)
        self.assertIn("MediaRecorder", html)
        self.assertIn("requestAnimationFrame", html)
        self.assertIn("开始测试", html)
        self.assertIn("停止测试", html)
        self.assertIn("下载录音", html)

    def test_mic_test_page_response_is_utf8_html(self):
        body = mic_test_server.build_mic_test_html().encode("utf-8")

        self.assertGreater(len(body), 1000)
        self.assertIn("真实 Mic 收声测试".encode("utf-8"), body)


if __name__ == "__main__":
    unittest.main()
