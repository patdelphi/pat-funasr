"""
程序说明：
测试 "app/openai_api/server.py" 的 "/v1/audio/transcriptions" 参数透传行为。

目标：
- 不加载真实模型，验证新增的离线识别增强参数能从 HTTP 表单进入 generate()。
- 覆盖运行时控制项与 VAD / batch_size_s 参数，避免只有函数级测试而缺少接口级回归。
"""

import importlib.util
import os
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_OPENAI_API_DIR = _ROOT / "app" / "openai_api"
_SERVER_PATH = _OPENAI_API_DIR / "server.py"


def _load_server_module():
    sys.path.insert(0, str(_OPENAI_API_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "funasr_openai_api_server_for_transcription_tests",
            _SERVER_PATH,
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载 server 模块：{_SERVER_PATH}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if sys.path and sys.path[0] == str(_OPENAI_API_DIR):
            sys.path.pop(0)


class _DummyModel:
    def __init__(self, capture_callback):
        self._capture_callback = capture_callback

    def generate(self, **kwargs):
        self._capture_callback(kwargs)
        return [{"text": "你好，世界。", "sentence_info": [{"start": 0, "end": 800, "text": "你好，世界。"}]}]


class TestServerTranscriptionsEndpoint(unittest.TestCase):
    def setUp(self):
        self.server = _load_server_module()
        self._orig_load_model = self.server.load_model
        self._orig_ffprobe_duration_s = self.server.segmentation.ffprobe_duration_s
        self._captured_load_kwargs = None
        self._captured_generate_kwargs = None

        def dummy_load_model(model_name: str, **kwargs):
            self._captured_load_kwargs = {"model_name": model_name, **kwargs}
            return _DummyModel(capture_callback=self._capture_generate_kwargs)

        self.server.load_model = dummy_load_model
        self.server.segmentation.ffprobe_duration_s = lambda _path: 2.5

        from fastapi.testclient import TestClient

        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.server.load_model = self._orig_load_model
        self.server.segmentation.ffprobe_duration_s = self._orig_ffprobe_duration_s

    def _capture_generate_kwargs(self, kwargs):
        self._captured_generate_kwargs = kwargs

    def test_transcriptions_forward_runtime_and_vad_controls(self):
        resp = self.client.post(
            "/v1/audio/transcriptions",
            data={
                "model": "paraformer",
                "response_format": "verbose_json",
                "language": "zh",
                "hotword": "项目名,术语",
                "use_itn": "true",
                "vad_preset": "anti_hallucination",
                "vad_max_single_segment_time": "15000",
                "merge_vad": "true",
                "merge_length_s": "12",
                "batch_size_s": "30",
                "punc_mode": "disabled",
                "device": "cpu",
                "hub": "ms",
                "disable_update": "false",
                "ncpu": "2",
                "log_level": "DEBUG",
                "disable_pbar": "true",
            },
            files={"file": ("demo.wav", b"\x00\x00" * 100, "audio/wav")},
        )
        self.assertEqual(resp.status_code, 200)

        self.assertIsNotNone(self._captured_load_kwargs)
        self.assertEqual(self._captured_load_kwargs["model_name"], "paraformer")
        self.assertEqual(self._captured_load_kwargs["device"], "cpu")
        self.assertEqual(self._captured_load_kwargs["hub"], "ms")
        self.assertEqual(self._captured_load_kwargs["disable_update"], False)
        self.assertEqual(self._captured_load_kwargs["ncpu"], 2)
        self.assertEqual(self._captured_load_kwargs["log_level"], "DEBUG")
        self.assertEqual(self._captured_load_kwargs["disable_pbar"], True)
        self.assertEqual(self._captured_load_kwargs["punc_mode"], "disabled")

        self.assertIsNotNone(self._captured_generate_kwargs)
        self.assertEqual(self._captured_generate_kwargs["language"], "Chinese")
        self.assertEqual(self._captured_generate_kwargs["hotword"], "项目名,术语")
        self.assertEqual(self._captured_generate_kwargs["use_itn"], True)
        self.assertEqual(self._captured_generate_kwargs["merge_vad"], True)
        self.assertEqual(self._captured_generate_kwargs["merge_length_s"], 12)
        self.assertEqual(self._captured_generate_kwargs["batch_size_s"], 30)
        self.assertEqual(
            self._captured_generate_kwargs["vad_kwargs"]["max_single_segment_time"],
            15000,
        )

    def test_transcriptions_preserves_uploaded_audio_suffix_without_wav_conversion(self):
        resp = self.client.post(
            "/v1/audio/transcriptions",
            data={"model": "paraformer", "response_format": "json"},
            files={"file": ("demo.mp3", b"ID3" + b"\x00" * 128, "audio/mpeg")},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(self._captured_generate_kwargs)
        self.assertTrue(self._captured_generate_kwargs["input"].endswith(".mp3"))

    def test_default_model_hub_can_come_from_environment(self):
        old_value = os.environ.get("FUNASR_MODEL_HUB")
        try:
            os.environ["FUNASR_MODEL_HUB"] = "hf"
            cfg = self.server.build_model_runtime_config(
                model_name="paraformer",
                device=None,
                hub=None,
                disable_update=None,
                ncpu=None,
                log_level=None,
                disable_pbar=None,
                punc_mode=None,
            )
        finally:
            if old_value is None:
                os.environ.pop("FUNASR_MODEL_HUB", None)
            else:
                os.environ["FUNASR_MODEL_HUB"] = old_value

        self.assertEqual(cfg["hub"], "hf")

    def test_qwen_structured_timestamps_drive_verbose_json_segments(self):
        class QwenDummyModel:
            def generate(_self, **kwargs):
                self._capture_generate_kwargs(kwargs)
                return [
                    {
                        "text": "你好。欢迎！",
                        "timestamp": [[100, 300], [1200, 1500], [1520, 1800], [1820, 2100]],
                        "timestamps": [
                            {"text": "你", "start_time": 0.1, "end_time": 0.3},
                            {"text": "好", "start_time": 1.2, "end_time": 1.5},
                            {"text": "欢", "start_time": 1.52, "end_time": 1.8},
                            {"text": "迎", "start_time": 1.82, "end_time": 2.1},
                        ],
                    }
                ]

        self.server.load_model = lambda _model_name, **_kwargs: QwenDummyModel()

        resp = self.client.post(
            "/v1/audio/transcriptions",
            data={"model": "qwen3-asr", "response_format": "verbose_json"},
            files={"file": ("demo.wav", b"\x00\x00" * 100, "audio/wav")},
        )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual([seg["text"] for seg in payload["segments"]], ["你好。", "欢迎！"])
        self.assertEqual(
            [(seg["start"], seg["end"]) for seg in payload["segments"]],
            [(0.1, 1.5), (1.52, 2.1)],
        )
        self.assertTrue(self._captured_generate_kwargs["output_timestamp"])

    def test_transcriptions_rejects_models_without_offline_asr_capability(self):
        for model in (
            "paraformer-zh-streaming",
            "emotion2vec-plus-large",
            "nllb-200-distilled-600m",
        ):
            with self.subTest(model=model):
                resp = self.client.post(
                    "/v1/audio/transcriptions",
                    data={"model": model, "response_format": "json"},
                    files={"file": ("demo.wav", b"\x00\x00", "audio/wav")},
                )
                self.assertEqual(resp.status_code, 400)
        self.assertIsNone(self._captured_generate_kwargs)

    def test_runtime_parameter_error_remains_http_400(self):
        def invalid_load_model(_model_name: str, **_kwargs):
            raise ValueError("invalid runtime option")

        self.server.load_model = invalid_load_model
        resp = self.client.post(
            "/v1/audio/transcriptions",
            data={"model": "paraformer", "response_format": "json"},
            files={"file": ("demo.wav", b"\x00\x00", "audio/wav")},
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"], "invalid runtime option")

    def test_run_asr_chunked_segments_apply_single_offset(self):
        """分块模式下 segments 只由 _merge_chunk_segments 加一次块偏移，
        防止 _run_asr 手动加偏移后再合并导致时间戳翻倍（#1 回归）。"""
        from pat_funasr_webui.fine_transcription import transcription_pipeline as tp

        # 模拟两个分块：块1 偏移 0 秒，块2 偏移 250 秒
        chunks = [("/tmp/chunk_0.wav", 0.0), ("/tmp/chunk_1.wav", 250.0)]

        def fake_build_segments(result0, duration_s, clean_text):
            return [
                {"start": 1.0, "end": 2.0, "text": "你好"},
                {"start": 3.0, "end": 4.0, "text": "世界"},
            ]

        orig_split = tp._split_audio_ffmpeg
        orig_safe_generate = self.server._safe_generate
        orig_ffprobe = self.server.segmentation.ffprobe_duration_s
        orig_build_segments = self.server.segmentation.build_segments
        try:
            tp._split_audio_ffmpeg = lambda *a, **kw: chunks
            self.server._safe_generate = lambda _model, _kwargs: [{"text": "你好世界"}]
            self.server.segmentation.ffprobe_duration_s = lambda _path: 10.0
            self.server.segmentation.build_segments = fake_build_segments
            result = self.server._run_asr(
                asr_model=object(),
                source_path="/tmp/demo.wav",
                generate_kwargs_base={},
                chunk_enabled=True,
                chunk_seconds=240.0,
                overlap_seconds=10.0,
                total_duration_s=260.0,
            )
        finally:
            tp._split_audio_ffmpeg = orig_split
            self.server._safe_generate = orig_safe_generate
            self.server.segmentation.ffprobe_duration_s = orig_ffprobe
            self.server.segmentation.build_segments = orig_build_segments

        starts = sorted(s["start"] for s in result["segments"])
        # 块1（offset=0）：1.0/3.0；块2（offset=250）：251.0/253.0；
        # 若双重偏移，块2 会变成 501.0/503.0
        self.assertEqual(starts, [1.0, 3.0, 251.0, 253.0])

    def test_merge_chunk_segments_keeps_distant_true_duplicates(self):
        """分块合并去重只作用于 2×overlap 窗口内，远距离真重复必须保留。"""
        from pat_funasr_webui.fine_transcription import transcription_pipeline as tp

        # 块1 "好的" start=10s；块2（offset=250）"好的" start=50+250=300s，间隔 290s >> 2*10s
        segs = [
            [{"start": 10.0, "end": 11.0, "text": "好的"}],
            [{"start": 50.0, "end": 51.0, "text": "好的"}],
        ]
        merged = tp._merge_chunk_segments(segs, [0.0, 250.0], overlap_seconds=10)
        self.assertEqual(len(merged), 2, "远距离真重复不应被去重")

    def test_merge_chunk_segments_dedupes_within_overlap_window(self):
        """重叠窗口（2×overlap）内的相同文本 + 近似时间戳应被去重。"""
        from pat_funasr_webui.fine_transcription import transcription_pipeline as tp

        # 块1 "好的" start=230s；块2（offset=240）"好的" start=10+240=250s，间隔 20s == 2*10s → 去重
        segs = [
            [{"start": 230.0, "end": 232.0, "text": "好的"}],
            [{"start": 10.0, "end": 12.0, "text": "好的"}],
        ]
        merged = tp._merge_chunk_segments(segs, [0.0, 240.0], overlap_seconds=10)
        self.assertEqual(len(merged), 1, "重叠窗口内的重复文本应被去重")

    def test_merge_chunk_segments_offsets_word_timestamps(self):
        """word 级时间戳需与段级时间戳同步加块偏移。"""
        from pat_funasr_webui.fine_transcription import transcription_pipeline as tp

        # 两块：第二块带 word 级时间戳，验证统一加偏移
        segs = [
            [{"start": 0.0, "end": 1.0, "text": "开头"}],
            [{"start": 0.0, "end": 2.0, "text": "你好世界",
              "words": [{"start": 0.0, "end": 1.0, "text": "你好"}, {"start": 1.0, "end": 2.0, "text": "世界"}]}],
        ]
        merged = tp._merge_chunk_segments(segs, [0.0, 100.0], overlap_seconds=10)
        second = [s for s in merged if s["start"] == 100.0][0]
        self.assertEqual(second["end"], 102.0)
        self.assertEqual(second["words"][0]["start"], 100.0)
        self.assertEqual(second["words"][1]["end"], 102.0)

    def test_merge_chunk_segments_empty_and_single_chunk(self):
        """空输入返回空列表；单块直接原样返回（不加偏移）。"""
        from pat_funasr_webui.fine_transcription import transcription_pipeline as tp

        self.assertEqual(tp._merge_chunk_segments([], []), [])
        single = [{"start": 1.0, "end": 2.0, "text": "单块"}]
        self.assertIs(tp._merge_chunk_segments([single], [0.0]), single)

    def test_run_asr_chunk_split_failure_falls_back_to_single(self):
        """ffmpeg 分块失败（chunks=0）时 _run_asr 应回退整文件单次识别，避免空结果。"""
        from pat_funasr_webui.fine_transcription import transcription_pipeline as tp

        generate_calls = []

        def fake_build_segments(result0, duration_s, clean_text):
            return [{"start": 0.0, "end": 2.0, "text": clean_text(result0.get("text", ""))}]

        orig_split = tp._split_audio_ffmpeg
        orig_safe_generate = self.server._safe_generate
        orig_ffprobe = self.server.segmentation.ffprobe_duration_s
        orig_build_segments = self.server.segmentation.build_segments
        try:
            tp._split_audio_ffmpeg = lambda *a, **kw: []
            # 记录 generate 收到的 input 路径，验证回退用的是整文件而非 chunk
            self.server._safe_generate = lambda model, kwargs: (
                generate_calls.append(kwargs.get("input")) or [{"text": "整文件识别"}]
            )
            self.server.segmentation.ffprobe_duration_s = lambda _path: 10.0
            self.server.segmentation.build_segments = fake_build_segments
            result = self.server._run_asr(
                asr_model=object(),
                source_path="/tmp/demo.wav",
                generate_kwargs_base={"input": "/tmp/demo.wav"},
                chunk_enabled=True,
                chunk_seconds=240.0,
                overlap_seconds=10.0,
                total_duration_s=10.0,
            )
        finally:
            tp._split_audio_ffmpeg = orig_split
            self.server._safe_generate = orig_safe_generate
            self.server.segmentation.ffprobe_duration_s = orig_ffprobe
            self.server.segmentation.build_segments = orig_build_segments

        self.assertEqual(generate_calls, ["/tmp/demo.wav"], "分块失败应回退整文件单次识别")
        self.assertEqual(result["text"], "整文件识别")
        self.assertEqual(len(result["segments"]), 1)

    def test_run_asr_empty_generate_returns_empty_text_with_fallback_segment(self):
        """ASR generate 返回空列表时：text 为空、segments 用时长兜底，不抛 IndexError。"""
        generate_calls = []

        orig_safe_generate = self.server._safe_generate
        orig_build_segments = self.server.segmentation.build_segments
        try:
            self.server._safe_generate = lambda model, kwargs: (
                generate_calls.append(True) or []
            )
            # build_segments 也返回空，验证最终兜底段
            self.server.segmentation.build_segments = lambda **kw: []
            result = self.server._run_asr(
                asr_model=object(),
                source_path="/tmp/demo.wav",
                generate_kwargs_base={},
                chunk_enabled=False,
                total_duration_s=30.0,
            )
        finally:
            self.server._safe_generate = orig_safe_generate
            self.server.segmentation.build_segments = orig_build_segments

        self.assertEqual(generate_calls, [True])
        self.assertEqual(result["text"], "")
        self.assertEqual(result["segments"], [{"start": 0.0, "end": 30.0, "text": "", "speaker": None}])

    def test_transcriptions_rejects_chunk_overlap_gte_chunk_seconds(self):
        """离线转写 API 校验 overlap_seconds >= chunk_seconds 时返回 400（对齐 workflow 校验）。"""
        import io

        resp = self.client.post(
            "/v1/audio/transcriptions",
            files={"file": ("demo.wav", io.BytesIO(b"\x00" * 4096), "audio/wav")},
            data={"model": "sensevoice", "chunk_enabled": "true", "chunk_seconds": "30", "overlap_seconds": "30"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("重叠秒数必须小于每块秒数", resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()
