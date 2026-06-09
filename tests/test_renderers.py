"""
程序说明：
输出渲染器单元测试（unittest）。

目标：
- 不依赖真实模型/GPU，仅用 mock segments 验证 txt/srt/vtt/tsv/json/all(zip) 输出正确性。
"""

import io
import unittest
import zipfile
import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_RENDERERS_PATH = _ROOT / "app" / "openai_api" / "renderers.py"
_SPEC = importlib.util.spec_from_file_location("funasr_openai_api_renderers", _RENDERERS_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"无法加载渲染器模块：{_RENDERERS_PATH}")
renderers = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(renderers)


class TestRenderers(unittest.TestCase):
    def setUp(self) -> None:
        self.full_text = "大家好今天开会讨论项目进度。第二部分讨论风险。"
        self.segments = [
            {"start": 0.00, "end": 3.20, "text": "大家好今天开会讨论项目进度。"},
            {"start": 3.20, "end": 6.50, "text": "第二部分讨论风险。"},
        ]
        self.meta = {"model": "sensevoice", "device": "cuda", "language": "zh"}

    def test_render_srt(self):
        got = renderers.render_srt(self.segments)
        expected = (
            "1\n"
            "00:00:00,000 --> 00:00:03,200\n"
            "大家好今天开会讨论项目进度。\n"
            "\n"
            "2\n"
            "00:00:03,200 --> 00:00:06,500\n"
            "第二部分讨论风险。\n"
            "\n"
        )
        self.assertEqual(got, expected)

    def test_render_tsv(self):
        got = renderers.render_tsv(self.segments)
        expected = (
            "0.00\t3.20\t大家好今天开会讨论项目进度。\n"
            "3.20\t6.50\t第二部分讨论风险。\n"
        )
        self.assertEqual(got, expected)

    def test_render_vtt_has_header(self):
        got = renderers.render_vtt(self.segments)
        self.assertTrue(got.startswith("WEBVTT\n"))
        self.assertIn("00:00:00.000 --> 00:00:03.200", got)
        self.assertIn("00:00:03.200 --> 00:00:06.500", got)

    def test_render_txt_wrap(self):
        got = renderers.render_txt(
            [{"start": 0.0, "end": 1.0, "text": "1234567890"}], max_line_width=4
        )
        self.assertEqual(got, "1234\n5678\n90\n")

    def test_render_with_speaker_prefix(self):
        speaker_segments = [
            {"start": 0.0, "end": 1.2, "text": "你好", "speaker": 0},
            {"start": 1.2, "end": 2.8, "text": "欢迎光临", "speaker": 1},
        ]
        txt = renderers.render_txt(speaker_segments)
        srt = renderers.render_srt(speaker_segments)
        tsv = renderers.render_tsv(speaker_segments)
        vtt = renderers.render_vtt(speaker_segments)
        self.assertIn("[spk=0] 你好", txt)
        self.assertIn("[spk=1] 欢迎光临", txt)
        self.assertIn("[spk=0] 你好", srt)
        self.assertIn("[spk=1] 欢迎光临", srt)
        self.assertIn("0.00\t1.20\t[spk=0] 你好", tsv)
        self.assertIn("[spk=1] 欢迎光临", vtt)

    def test_render_all_zip(self):
        payload = renderers.build_verbose_json_payload(
            full_text=self.full_text, segments=self.segments, meta=self.meta
        )
        zbytes = renderers.render_all_zip(
            full_text=self.full_text,
            segments=self.segments,
            json_payload=payload,
        )
        zf = zipfile.ZipFile(io.BytesIO(zbytes))
        names = set(zf.namelist())
        self.assertEqual(
            names, {"output.txt", "output.tsv", "output.srt", "output.vtt", "output.json"}
        )
        self.assertEqual(zf.read("output.srt").decode("utf-8"), renderers.render_srt(self.segments))
        self.assertEqual(zf.read("output.tsv").decode("utf-8"), renderers.render_tsv(self.segments))


if __name__ == "__main__":
    unittest.main()
