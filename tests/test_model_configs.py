"""
程序说明：
模型别名与 model id 映射的单元测试（unittest）。

目标：
- 避免把别名与官方文档口径搞混，确保 server.py 的 MODEL_CONFIGS 与文档一致。
- 测试只做静态配置校验，不触发模型加载/下载。
"""

import sys
import unittest
import importlib.util
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_OPENAI_API_DIR = _ROOT / "app" / "openai_api"
_SERVER_PATH = _OPENAI_API_DIR / "server.py"


def _load_server_module():
    sys.path.insert(0, str(_OPENAI_API_DIR))
    try:
        spec = importlib.util.spec_from_file_location("funasr_openai_api_server", _SERVER_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载 server 模块：{_SERVER_PATH}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if sys.path and sys.path[0] == str(_OPENAI_API_DIR):
            sys.path.pop(0)


class TestModelConfigs(unittest.TestCase):
    def test_model_id_mappings_align_official(self):
        server = _load_server_module()
        cfgs = server.MODEL_CONFIGS

        self.assertEqual(cfgs["fun-asr-nano"]["model"], "FunAudioLLM/Fun-ASR-Nano-2512")
        self.assertEqual(cfgs["fun-asr-nano"].get("hub"), "hf")
        self.assertTrue(cfgs["fun-asr-nano"].get("trust_remote_code"))

        self.assertEqual(cfgs["qwen3-asr"]["model"], "Qwen/Qwen3-ASR-1.7B")
        self.assertEqual(cfgs["qwen3-asr"].get("hub"), "hf")
        self.assertTrue(cfgs["qwen3-asr"].get("trust_remote_code"))

        self.assertEqual(cfgs["qwen3-asr-0.6b"]["model"], "Qwen/Qwen3-ASR-0.6B")
        self.assertEqual(cfgs["qwen3-asr-0.6b"].get("hub"), "hf")
        self.assertTrue(cfgs["qwen3-asr-0.6b"].get("trust_remote_code"))


if __name__ == "__main__":
    unittest.main()

