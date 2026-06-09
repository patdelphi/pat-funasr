"""
程序说明：
测试 "app/openai_api/server.py" 的 generate 参数白名单构建逻辑。

目标：
- 确保 Pat WebUI 新增参数只会透传后端允许的字段。
- 确保 `hotword`、`use_itn`、`vad_preset`、`merge_vad` 等参数拼装稳定。
"""

import importlib.util
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_OPENAI_API_DIR = _ROOT / "app" / "openai_api"
_SERVER_PATH = _OPENAI_API_DIR / "server.py"


def _load_server_module():
    sys.path.insert(0, str(_OPENAI_API_DIR))
    try:
        spec = importlib.util.spec_from_file_location("funasr_openai_api_server_for_kwargs", _SERVER_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载 server 模块：{_SERVER_PATH}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if sys.path and sys.path[0] == str(_OPENAI_API_DIR):
            sys.path.pop(0)


class TestServerGenerateKwargs(unittest.TestCase):
    def setUp(self):
        self.server = _load_server_module()

    def test_build_generate_kwargs_with_extended_fields(self):
        got = self.server.build_generate_kwargs(
            tmp_path="demo.wav",
            model="paraformer",
            language="zh",
            hotword="项目名,术语",
            use_itn=True,
            vad_preset="anti_hallucination",
            merge_vad=True,
            merge_length_s=12,
            batch_size_s=30,
            vad_max_single_segment_time=15000,
        )

        self.assertEqual(got["input"], "demo.wav")
        self.assertEqual(got["batch_size"], 1)
        self.assertEqual(got["language"], "zh")
        self.assertEqual(got["hotword"], "项目名,术语")
        self.assertEqual(got["use_itn"], True)
        self.assertEqual(got["sentence_timestamp"], True)
        self.assertEqual(got["merge_vad"], True)
        self.assertEqual(got["merge_length_s"], 12)
        self.assertEqual(got["batch_size_s"], 30)
        self.assertEqual(got["vad_kwargs"]["max_single_segment_time"], 15000)
        self.assertIn("max_end_silence_time", got)

    def test_build_generate_kwargs_skips_optional_empty_values(self):
        got = self.server.build_generate_kwargs(
            tmp_path="demo.wav",
            model="sensevoice",
            language=None,
            hotword=None,
            use_itn=None,
            vad_preset=None,
            merge_vad=None,
            merge_length_s=None,
            batch_size_s=None,
            vad_max_single_segment_time=None,
        )

        self.assertEqual(
            got,
            {
                "input": "demo.wav",
                "batch_size": 1,
            },
        )

    def test_build_model_runtime_config_applies_runtime_overrides(self):
        cfg = self.server.build_model_runtime_config(
            model_name="paraformer",
            device="cpu",
            hub="ms",
            disable_update=False,
            ncpu=2,
            log_level="debug",
            disable_pbar=True,
            punc_mode="disabled",
        )
        self.assertEqual(cfg["device"], "cpu")
        self.assertEqual(cfg["hub"], "ms")
        self.assertEqual(cfg["disable_update"], False)
        self.assertEqual(cfg["ncpu"], 2)
        self.assertEqual(cfg["log_level"], "DEBUG")
        self.assertEqual(cfg["disable_pbar"], True)
        self.assertNotIn("punc_model", cfg)

    def test_build_model_runtime_config_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            self.server.build_model_runtime_config(
                model_name="paraformer",
                device=None,
                hub=None,
                disable_update=None,
                ncpu=0,
                log_level=None,
                disable_pbar=None,
                punc_mode="auto",
            )
        with self.assertRaises(ValueError):
            self.server.build_model_runtime_config(
                model_name="paraformer",
                device=None,
                hub=None,
                disable_update=None,
                ncpu=None,
                log_level="trace",
                disable_pbar=None,
                punc_mode="auto",
            )


if __name__ == "__main__":
    unittest.main()
