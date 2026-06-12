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


if __name__ == "__main__":
    unittest.main()
