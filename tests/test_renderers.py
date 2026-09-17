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
            names, {"transcript.txt", "transcript.tsv", "transcript.srt", "transcript.vtt", "transcript.json"}
        )
        # ZIP 内统一做了 CRLF 转换，测试先转回 LF 再比
        self.assertEqual(zf.read("transcript.srt").decode("utf-8-sig").replace("\r\n", "\n"), renderers.render_srt(self.segments))
        self.assertEqual(zf.read("transcript.tsv").decode("utf-8-sig").replace("\r\n", "\n"), renderers.render_tsv(self.segments))

    def test_render_csv(self):
        """CSV 应有表头、按段逐行输出，含逗号/引号时正确转义。"""
        got = renderers.render_csv(self.segments)
        lines = got.split("\n")
        self.assertEqual(lines[0], "start,end,speaker,text")
        self.assertEqual(lines[1], "0.00,3.20,,大家好今天开会讨论项目进度。")
        self.assertEqual(lines[2], "3.20,6.50,,第二部分讨论风险。")

        quoted = renderers.render_csv(
            [{"start": 0.0, "end": 1.0, "text": '含"引号"与,逗号', "speaker": "S1"}]
        )
        self.assertIn('"含""引号""与,逗号"', quoted)
        self.assertIn("S1", quoted)

    def test_render_docx(self):
        """DOCX 是合法 ZIP，含标准部件，正文需 XML 转义。"""
        segs = [
            {"start": 0.0, "end": 3.2, "text": "你好 & <世界>", "speaker": 0},
            {"start": 3.2, "end": 6.5, "text": "第二部分", "speaker": 1},
        ]
        data = renderers.render_docx(segs, title="会议记录")
        zf = zipfile.ZipFile(io.BytesIO(data))
        self.assertEqual(
            set(zf.namelist()),
            {"[Content_Types].xml", "_rels/.rels", "word/document.xml"},
        )
        xml = zf.read("word/document.xml").decode("utf-8")
        self.assertIn("会议记录", xml)
        self.assertIn("你好 &amp; &lt;世界&gt;", xml)
        self.assertIn("[spk=0]", xml)
        self.assertIn("[spk=1]", xml)
        # 时间戳使用 SRT 风格便于阅读（'>' 在 XML 中转义为 &gt;）
        self.assertIn("00:00:00,000 --&gt; 00:00:03,200", xml)

    def test_render_all_zip_extra_formats(self):
        """extra_formats 指定时追加 csv/docx；默认不追加，保持原 5 件套。"""
        payload = renderers.build_verbose_json_payload(
            full_text=self.full_text, segments=self.segments, meta=self.meta
        )
        zbytes = renderers.render_all_zip(
            full_text=self.full_text,
            segments=self.segments,
            json_payload=payload,
            extra_formats=["csv", "docx"],
        )
        zf = zipfile.ZipFile(io.BytesIO(zbytes))
        names = set(zf.namelist())
        self.assertIn("transcript.csv", names)
        self.assertIn("transcript.docx", names)
        # docx 是二进制，不应带 BOM/CRLF 转换
        self.assertTrue(zf.read("transcript.docx").startswith(b"PK"))

        plain = renderers.render_all_zip(
            full_text=self.full_text,
            segments=self.segments,
            json_payload=payload,
        )
        self.assertNotIn("transcript.csv", set(zipfile.ZipFile(io.BytesIO(plain)).namelist()))


if __name__ == "__main__":
    unittest.main()
