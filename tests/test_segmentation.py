"""
程序说明：
测试 "app/openai_api/segmentation.py" 的文本分段与时间戳生成逻辑。
"""

import importlib.util
import pathlib
import unittest


def _load_module_from_path(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块：{path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSegmentation(unittest.TestCase):
    def setUp(self):
        repo = pathlib.Path(__file__).resolve().parents[1]
        self.seg = _load_module_from_path("segmentation_mod", repo / "app" / "openai_api" / "segmentation.py")

    def test_build_segments_from_text_with_punc(self):
        text = "你好，欢迎光临。咱们有预约吗？没有也没关系！"
        clean = lambda s: s.strip()
        segs = self.seg.build_segments_from_text(text, duration_s=10.0, clean_text=clean)
        self.assertGreaterEqual(len(segs), 2)
        self.assertAlmostEqual(segs[0]["start"], 0.0, places=3)
        self.assertAlmostEqual(segs[-1]["end"], 10.0, places=3)
        self.assertTrue(all(s["text"] for s in segs))

    def test_build_segments_from_text_without_punc(self):
        text = "这是一个没有标点也很长的句子为了验证会被切成多段而不是只有一整段输出" * 2
        clean = lambda s: s.strip()
        segs = self.seg.build_segments_from_text(text, duration_s=12.0, clean_text=clean)
        self.assertGreaterEqual(len(segs), 2)
        self.assertAlmostEqual(segs[-1]["end"], 12.0, places=3)

    def test_build_segments_from_sentence_info(self):
        clean = lambda s: s.strip()
        sentence_info = [
            {"start": 0, "end": 1000, "text": "A", "spk": 0},
            {"start": 1500, "end": 2800, "text": "B", "spk": 1},
        ]
        segs = self.seg.build_segments_from_sentence_info(sentence_info, clean_text=clean)
        self.assertEqual(len(segs), 2)
        self.assertAlmostEqual(segs[0]["end"], 1.0, places=3)
        self.assertAlmostEqual(segs[1]["start"], 1.5, places=3)
        self.assertAlmostEqual(segs[1]["end"], 2.8, places=3)
        self.assertEqual(segs[0]["speaker"], 0)
        self.assertEqual(segs[1]["speaker"], 1)

    def test_build_segments_uses_qwen_structured_timestamps(self):
        clean = lambda s: s.strip()
        result = {
            "text": "你好，世界。今天很好！",
            "timestamp": [
                [100, 200], [220, 320], [500, 620], [640, 760],
                [1400, 1520], [1540, 1660], [1680, 1800], [1820, 1940],
            ],
            "timestamps": [
                {"text": "你", "start_time": 0.1, "end_time": 0.2},
                {"text": "好", "start_time": 0.22, "end_time": 0.32},
                {"text": "世", "start_time": 0.5, "end_time": 0.62},
                {"text": "界", "start_time": 0.64, "end_time": 0.76},
                {"text": "今", "start_time": 1.4, "end_time": 1.52},
                {"text": "天", "start_time": 1.54, "end_time": 1.66},
                {"text": "很", "start_time": 1.68, "end_time": 1.8},
                {"text": "好", "start_time": 1.82, "end_time": 1.94},
            ],
        }

        segs = self.seg.build_segments(result0=result, duration_s=3.0, clean_text=clean)

        self.assertEqual([seg["text"] for seg in segs], ["你好，世界。", "今天很好！"])
        self.assertEqual((segs[0]["start"], segs[0]["end"]), (0.1, 0.76))
        self.assertEqual((segs[1]["start"], segs[1]["end"]), (1.4, 1.94))

    def test_build_segments_matches_english_words_case_insensitively(self):
        clean = lambda s: s.strip()
        result = {
            "text": "Hello world. This is fine!",
            "timestamps": [
                {"text": "hello", "start_time": 0.2, "end_time": 0.5},
                {"text": "world", "start_time": 0.6, "end_time": 0.9},
                {"text": "This", "start_time": 1.5, "end_time": 1.8},
                {"text": "is", "start_time": 1.9, "end_time": 2.0},
                {"text": "fine", "start_time": 2.1, "end_time": 2.5},
            ],
        }

        segs = self.seg.build_segments(result0=result, duration_s=3.0, clean_text=clean)

        self.assertEqual(len(segs), 2)
        self.assertEqual((segs[0]["start"], segs[0]["end"]), (0.2, 0.9))
        self.assertEqual((segs[1]["start"], segs[1]["end"]), (1.5, 2.5))

    def test_build_segments_falls_back_when_qwen_tokens_do_not_match(self):
        clean = lambda s: s.strip()
        result = {
            "text": "甲。乙！",
            "timestamp": [[100, 300], [1200, 1500]],
            "timestamps": [
                {"text": "错误", "start_time": 0.1, "end_time": 0.3},
                {"text": "内容", "start_time": 1.2, "end_time": 1.5},
            ],
        }

        segs = self.seg.build_segments(result0=result, duration_s=2.0, clean_text=clean)

        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]["start"], 0.1)
        self.assertEqual(segs[-1]["end"], 1.5)


if __name__ == "__main__":
    unittest.main()
