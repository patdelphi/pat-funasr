# -*- coding: utf-8 -*-
"""
程序说明：
测试 "app/pat_funasr_webui/translation_utils.py" 的文本与文件翻译处理逻辑。

目标：
- 验证 srt/vtt/tsv/json 格式解析并安全翻译。
- 验证长文本切分算法，防止长文本推理溢出。
"""

import unittest
from pathlib import Path
import tempfile
import json
import importlib.util
import sys

_ROOT = Path(__file__).resolve().parents[1]
_WEBUI_DIR = _ROOT / "app" / "pat_funasr_webui"
_TRANS_UTILS_PATH = _WEBUI_DIR / "translation_utils.py"


def _load_translation_utils():
    sys.path.insert(0, str(_WEBUI_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "funasr_pat_webui_translation_utils_tests",
            _TRANS_UTILS_PATH,
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载 translation_utils 模块：{_TRANS_UTILS_PATH}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if sys.path and sys.path[0] == str(_WEBUI_DIR):
            sys.path.pop(0)


class TestPatWebUiTranslationUtils(unittest.TestCase):
    def setUp(self):
        self.utils = _load_translation_utils()
        self.mock_translations = []

        # Mock request_translation 避免触发真实后端网络调用
        def mock_request_translation(base_url, text, source_lang, target_lang, model, timeout):
            self.mock_translations.append(text)
            if isinstance(text, list):
                return [f"[{source_lang}->{target_lang}]: {t}" for t in text]
            return f"[{source_lang}->{target_lang}]: {text}"

        self.utils.request_translation = mock_request_translation

    def test_split_text_by_length(self):
        # 简单句切分
        long_text = "这是第一句。这是第二句！这是第三句？后面是个长词语"
        chunks = self.utils.split_text_by_length(long_text, max_chars=10)
        self.assertGreater(len(chunks), 1)
        self.assertIn("这是第一句。", chunks)
        self.assertIn("这是第二句！", chunks)

    def test_translate_srt_keeps_timestamps(self):
        srt_content = (
            "1\n"
            "00:00:01,000 --> 00:00:04,000\n"
            "Hello world\n\n"
            "2\n"
            "00:00:04,500 --> 00:00:08,200\n"
            "How are you?\n"
        )
        translated = self.utils.translate_srt(
            "http://127.0.0.1:8000",
            srt_content,
            "eng_Latn",
            "zho_Hans",
            "nllb-200-distilled-600m",
            10
        )
        self.assertIn("00:00:01,000 --> 00:00:04,000", translated)
        self.assertIn("[eng_Latn->zho_Hans]: Hello world", translated)
        self.assertIn("[eng_Latn->zho_Hans]: How are you?", translated)

    def test_translate_vtt_keeps_timestamps(self):
        vtt_content = (
            "WEBVTT\n\n"
            "1\n"
            "00:01.000 --> 00:04.000\n"
            "Hello world\n"
        )
        translated = self.utils.translate_vtt(
            "http://127.0.0.1:8000",
            vtt_content,
            "eng_Latn",
            "zho_Hans",
            "nllb-200-distilled-600m",
            10
        )
        self.assertTrue(translated.startswith("WEBVTT"))
        self.assertIn("00:01.000 --> 00:04.000", translated)
        self.assertIn("[eng_Latn->zho_Hans]: Hello world", translated)

    def test_translate_tsv_keeps_columns(self):
        tsv_content = (
            "start\tend\ttext\tspeaker\n"
            "0.0\t1.5\tHello\t0\n"
            "1.8\t3.2\tWorld\t1\n"
        )
        translated = self.utils.translate_tsv(
            "http://127.0.0.1:8000",
            tsv_content,
            "eng_Latn",
            "zho_Hans",
            "nllb-200-distilled-600m",
            10
        )
        self.assertIn("start\tend\ttext\tspeaker", translated)
        self.assertIn("[eng_Latn->zho_Hans]: Hello", translated)
        self.assertIn("[eng_Latn->zho_Hans]: World", translated)

    def test_translate_json_keeps_structure(self):
        js = {
            "text": "Hello world",
            "segments": [
                {"start": 0.0, "end": 2.0, "text": "Hello"},
                {"start": 2.0, "end": 4.0, "text": "world"}
            ]
        }
        translated_str = self.utils.translate_json(
            "http://127.0.0.1:8000",
            json.dumps(js),
            "eng_Latn",
            "zho_Hans",
            "nllb-200-distilled-600m",
            10
        )
        data = json.loads(translated_str)
        self.assertEqual(data["text"], "[eng_Latn->zho_Hans]: Hello world")
        self.assertEqual(data["segments"][0]["text"], "[eng_Latn->zho_Hans]: Hello")

    def test_convert_to_chinese_punctuation(self):
        raw_text = 'Hello, how are you? "Fine, thank you!" Yes: it is 12:30. http://localhost. 3.14 is pi.'
        expected = 'Hello，how are you？“Fine，thank you！” Yes：it is 12:30。http://localhost。3.14 is pi。'
        result = self.utils.convert_to_chinese_punctuation(raw_text)
        self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
