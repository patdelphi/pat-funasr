"""
程序说明：
测试 "aipython/asr_b5_regression.py" 的 multipart 构建逻辑（unittest）。

目标：
- 不依赖服务端，仅校验 multipart body 的关键字段存在。
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _ROOT / "aipython" / "asr_b5_regression.py"
_SPEC = importlib.util.spec_from_file_location("funasr_asr_b5_regression", _SCRIPT_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"无法加载脚本：{_SCRIPT_PATH}")
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


class TestAsrB5Regression(unittest.TestCase):
    def test_parse_formats(self):
        self.assertEqual(mod.parse_formats("json, verbose_json"), ["json", "verbose_json"])
        self.assertEqual(mod.parse_formats(""), [])

    def test_multipart_body_contains_fields(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(b"RIFF0000WAVEfmt ")
            tmp_path = Path(tmp.name)

        body, boundary = mod.multipart_body(
            tmp_path,
            {
                "model": "sensevoice",
                "response_format": "verbose_json",
            },
        )

        text = body.decode("utf-8", errors="replace")
        self.assertIn(boundary, text)
        self.assertIn('name="file"', text)
        self.assertIn('name="model"', text)
        self.assertIn("sensevoice", text)
        self.assertIn('name="response_format"', text)
        self.assertIn("verbose_json", text)


if __name__ == "__main__":
    unittest.main()
