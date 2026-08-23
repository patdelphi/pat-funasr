"""
程序说明：
精细转录管线的正确性回归测试。

目标：
- 确保音频前处理调用真实存在的共享函数，失败时不再静默回退。
- 确保启用说话人分离的场景进入 diarization 端点。
- 确保 ASR 请求结束后关闭上传文件句柄。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


_ROOT = Path(__file__).resolve().parents[1]
_WEBUI_DIR = _ROOT / "app" / "pat_funasr_webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))

from fine_transcription import audio_processor  # noqa: E402
from fine_transcription import transcription_pipeline as pipeline  # noqa: E402
from fine_transcription.scene_templates import SceneTemplate  # noqa: E402


class _DummyResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class TestFineTranscriptionPipeline(unittest.TestCase):
    def test_preprocess_uses_process_audio_and_returns_output_path(self):
        with mock.patch.object(
            audio_processor,
            "process_audio",
            return_value=("processed.wav", {"duration": 1}, {"duration": 1}),
        ) as mocked:
            result = pipeline._preprocess_audio_if_needed(
                "input.wav",
                enable_preprocess=True,
                denoise_strength=9,
                enable_vad=True,
            )

        self.assertEqual(result, "processed.wav")
        mocked.assert_called_once_with(
            "input.wav",
            noise_reduction=True,
            noise_strength=9,
            sample_rate=16000,
            vad_enabled=True,
            loudnorm=True,
        )

    def test_preprocess_failure_is_visible_to_pipeline(self):
        with mock.patch.object(
            audio_processor,
            "process_audio",
            side_effect=RuntimeError("ffmpeg failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "ffmpeg failed"):
                pipeline._preprocess_audio_if_needed(
                    "input.wav",
                    enable_preprocess=True,
                    denoise_strength=8,
                    enable_vad=False,
                )

    def test_diarization_scene_calls_diarization_endpoint(self):
        template = SceneTemplate(
            scene_id="meeting-test",
            name="会议测试",
            description="测试",
            asr_params={"diarization": True},
        )
        captured = {}

        def fake_post(url, *, files, data, timeout):
            captured["url"] = url
            captured["handle"] = files["file"][1]
            captured["data"] = data
            return _DummyResponse(
                {
                    "text": "你好",
                    "segments": [
                        {"start": 0.0, "end": 1.0, "text": "你好", "speaker": 0}
                    ],
                    "speakers": [0],
                }
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "meeting.wav"
            audio_path.write_bytes(b"\x00\x00" * 100)
            with mock.patch.object(pipeline.requests, "post", side_effect=fake_post):
                result = pipeline._call_asr(
                    str(audio_path),
                    model="paraformer",
                    template=template,
                    hotwords=["项目名"],
                    base_url="http://localhost:8000",
                )

        self.assertTrue(captured["url"].endswith("/v1/funasr/diarization"))
        self.assertEqual(captured["data"]["spk_model"], "cam++")
        self.assertEqual(result["segments"][0]["speaker"], 0)
        self.assertTrue(captured["handle"].closed)

    def test_standard_asr_closes_uploaded_file_handle(self):
        captured = {}

        def fake_post(url, *, files, data, timeout):
            captured["url"] = url
            captured["handle"] = files["file"][1]
            return _DummyResponse({"text": "hello", "segments": []})

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "standard.wav"
            audio_path.write_bytes(b"\x00\x00" * 100)
            with mock.patch.object(pipeline.requests, "post", side_effect=fake_post):
                pipeline._call_asr(
                    str(audio_path),
                    model="sensevoice",
                    template=None,
                    base_url="http://localhost:8000",
                )

        self.assertTrue(captured["url"].endswith("/v1/audio/transcriptions"))
        self.assertTrue(captured["handle"].closed)

    def test_merge_chunk_segments_deduplicates_overlap_with_shifted_timestamps(self):
        merged = pipeline._merge_chunk_segments(
            [
                [{"start": 235.0, "end": 240.0, "text": "重叠句。"}],
                [{"start": 5.8, "end": 10.0, "text": "重叠句。"}],
            ],
            [0.0, 230.0],
            overlap_seconds=10,
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["text"], "重叠句。")


if __name__ == "__main__":
    unittest.main()
