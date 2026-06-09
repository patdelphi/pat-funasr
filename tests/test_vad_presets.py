"""
程序说明：
VAD 预设映射单元测试（unittest）。
"""

import unittest

import importlib.util
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_PRESETS_PATH = _ROOT / "app" / "openai_api" / "vad_presets.py"
_SPEC = importlib.util.spec_from_file_location("funasr_openai_api_vad_presets", _PRESETS_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"无法加载模块：{_PRESETS_PATH}")
vad_presets = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vad_presets)


class TestVadPresets(unittest.TestCase):
    def test_allowed_presets(self):
        self.assertIn("default", vad_presets.allowed_presets())
        self.assertIn("anti_hallucination", vad_presets.allowed_presets())

    def test_default_preset_is_empty(self):
        self.assertEqual(vad_presets.build_vad_kwargs_for_preset("default"), {})

    def test_anti_hallucination_has_keys(self):
        cfg = vad_presets.build_vad_kwargs_for_preset("anti_hallucination")
        self.assertIn("max_start_silence_time", cfg)
        self.assertIn("max_end_silence_time", cfg)

    def test_apply_controls(self):
        out = vad_presets.apply_vad_controls(
            generate_kwargs={"input": "a.wav"},
            vad_preset="anti_hallucination",
            merge_vad=True,
            merge_length_s=10,
            vad_max_single_segment_time=15000,
        )
        self.assertEqual(out["merge_vad"], True)
        self.assertEqual(out["merge_length_s"], 10)
        self.assertEqual(out["vad_kwargs"]["max_single_segment_time"], 15000)
        self.assertIn("max_end_silence_time", out)

    def test_merge_length_must_be_positive(self):
        with self.assertRaises(ValueError):
            vad_presets.apply_vad_controls(
                generate_kwargs={},
                vad_preset=None,
                merge_vad=None,
                merge_length_s=0,
                vad_max_single_segment_time=None,
            )

    def test_vad_max_single_segment_time_must_be_positive(self):
        with self.assertRaises(ValueError):
            vad_presets.apply_vad_controls(
                generate_kwargs={},
                vad_preset=None,
                merge_vad=None,
                merge_length_s=None,
                vad_max_single_segment_time=0,
            )


if __name__ == "__main__":
    unittest.main()
