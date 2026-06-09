"""
程序说明：
测试 "Pat WebUI" 的说话人分离导出文件生成逻辑（unittest）。

目标：
- 验证前端在拿到 diarization JSON 后，能本地生成 json/txt/srt/vtt/tsv/zip 下载文件。
- 验证导出文本中保留 speaker 前缀，避免 UI 下载内容与后端渲染口径不一致。
"""

import importlib.util
import io
import json
import unittest
import warnings
import zipfile
from pathlib import Path

warnings.filterwarnings(
    "ignore",
    category=ResourceWarning,
    module=r"asyncio\.base_events",
)

_ROOT = Path(__file__).resolve().parents[1]
_GRADIO_APP_PATH = _ROOT / "app" / "pat_funasr_webui" / "gradio_app.py"
_SPEC = importlib.util.spec_from_file_location("funasr_pat_gradio_app", _GRADIO_APP_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"无法加载 Pat WebUI 模块：{_GRADIO_APP_PATH}")
gradio_app = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gradio_app)


class TestPatWebUiDiarizationExports(unittest.TestCase):
    def test_build_diarization_export_files(self):
        payload = {
            "model": "paraformer",
            "spk_model": "cam++",
            "spk_mode": "punc_segment",
            "text": "你好 欢迎光临",
            "speakers": [0, 1],
            "segments": [
                {"start": 0.0, "end": 1.2, "text": "你好", "speaker": 0},
                {"start": 1.2, "end": 2.8, "text": "欢迎光临", "speaker": 1},
            ],
        }

        exports = gradio_app.build_diarization_export_files(payload)

        self.assertEqual(set(exports.keys()), {"json", "txt", "srt", "vtt", "tsv", "all"})
        for file_path in exports.values():
            self.assertTrue(Path(file_path).exists(), msg=f"导出文件不存在：{file_path}")

        json_text = Path(exports["json"]).read_text(encoding="utf-8")
        txt_text = Path(exports["txt"]).read_text(encoding="utf-8")
        srt_text = Path(exports["srt"]).read_text(encoding="utf-8")
        vtt_text = Path(exports["vtt"]).read_text(encoding="utf-8")
        tsv_text = Path(exports["tsv"]).read_text(encoding="utf-8")

        loaded_payload = json.loads(json_text)
        self.assertEqual(loaded_payload["segments"][0]["speaker"], 0)
        self.assertIn("[spk=0] 你好", txt_text)
        self.assertIn("[spk=1] 欢迎光临", txt_text)
        self.assertIn("[spk=0] 你好", srt_text)
        self.assertIn("[spk=1] 欢迎光临", vtt_text)
        self.assertIn("0.00\t1.20\t[spk=0] 你好", tsv_text)

        archive_bytes = Path(exports["all"]).read_bytes()
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            self.assertEqual(
                set(zf.namelist()),
                {"output.json", "output.srt", "output.tsv", "output.txt", "output.vtt"},
            )
            self.assertIn("[spk=0] 你好", zf.read("output.txt").decode("utf-8"))

    def test_build_transcription_export_files(self):
        payload = {
            "text": "你好，欢迎光临。",
            "segments": [
                {"start": 0.0, "end": 1.2, "text": "你好，", "speaker": None},
                {"start": 1.2, "end": 2.8, "text": "欢迎光临。", "speaker": None},
            ],
            "language": "zh",
        }

        exports = gradio_app.build_transcription_export_files(payload)

        self.assertEqual(set(exports.keys()), {"json", "txt", "srt", "vtt", "tsv", "all"})
        txt_text = Path(exports["txt"]).read_text(encoding="utf-8")
        srt_text = Path(exports["srt"]).read_text(encoding="utf-8")
        self.assertIn("你好，", txt_text)
        self.assertIn("欢迎光临。", txt_text)
        self.assertIn("00:00:00,000 --> 00:00:01,200", srt_text)

    def test_update_transcription_preview(self):
        payload_json = json.dumps(
            {
                "text": "你好，欢迎光临。",
                "segments": [
                    {"start": 0.0, "end": 1.2, "text": "你好，", "speaker": None},
                    {"start": 1.2, "end": 2.8, "text": "欢迎光临。", "speaker": None},
                ],
            },
            ensure_ascii=False,
        )
        preview_json = gradio_app.update_transcription_preview("json", payload_json)
        preview_srt = gradio_app.update_transcription_preview("srt", payload_json)
        self.assertIn('"text": "你好，欢迎光临。"', preview_json)
        self.assertIn("00:00:00,000 --> 00:00:01,200", preview_srt)

    def test_update_diarization_preview(self):
        payload_json = json.dumps(
            {
                "text": "你好 欢迎光临",
                "speakers": [0, 1],
                "segments": [
                    {"start": 0.0, "end": 1.2, "text": "你好", "speaker": 0},
                    {"start": 1.2, "end": 2.8, "text": "欢迎光临", "speaker": 1},
                ],
            },
            ensure_ascii=False,
        )
        preview_txt = gradio_app.update_diarization_preview("txt", payload_json)
        preview_json = gradio_app.update_diarization_preview("json", payload_json)
        self.assertIn("[spk=0] 你好", preview_txt)
        self.assertIn('"speakers": [', preview_json)

    def test_update_media_preview_for_audio(self):
        video_update, audio_update, status = gradio_app.update_media_preview(
            r"y:\NewStore\AI\FunASR-Portable-GPU\test\demo.wav"
        )
        self.assertEqual(video_update["visible"], False)
        self.assertEqual(audio_update["visible"], True)
        self.assertEqual(audio_update["value"], r"y:\NewStore\AI\FunASR-Portable-GPU\test\demo.wav")
        self.assertIn("已加载音频", status)

    def test_format_streaming_preview_text(self):
        formatted = gradio_app.format_streaming_preview_text(
            full_text="你好欢迎光临林肯先生。咱们是有预约吗？先生然后这边请",
            final_flag=True,
        )
        self.assertNotIn("\n", formatted)
        self.assertEqual(formatted, "你好欢迎光临林肯先生。咱们是有预约吗？先生然后这边请")

    def test_build_app_contains_service_controls(self):
        demo = gradio_app.build_app("http://127.0.0.1:8000", 300)
        try:
            labels = {
                component.get("props", {}).get("label")
                for component in demo.config.get("components", [])
            }
            button_values = {
                component.get("props", {}).get("value")
                for component in demo.config.get("components", [])
                if component.get("type") == "button"
            }
            self.assertIn("模型状态", labels)
            self.assertIn("刷新模型列表", button_values)
            self.assertIn("检查服务", button_values)
        finally:
            if hasattr(demo, "close"):
                demo.close()

    def test_build_app_falls_back_for_function_model_dropdowns(self):
        original_fetch_model_choices = gradio_app.fetch_model_choices
        try:
            gradio_app.fetch_model_choices = lambda *_args, **_kwargs: (
                [("Qwen3-ASR-1.7B (qwen3-asr) [ready]", "qwen3-asr")],
                "mock",
            )
            demo = gradio_app.build_app("http://127.0.0.1:8000", 300)
        finally:
            gradio_app.fetch_model_choices = original_fetch_model_choices

        try:
            dropdowns = {
                component.get("props", {}).get("label"): component.get("props", {})
                for component in demo.config.get("components", [])
                if component.get("type") == "dropdown"
            }
            self.assertEqual(dropdowns["Streaming 模型"]["value"], "paraformer-zh-streaming")
            self.assertEqual(dropdowns["情感模型"]["value"], "emotion2vec-plus-large")
            self.assertEqual(dropdowns["识别模型"]["value"], "paraformer")
        finally:
            if hasattr(demo, "close"):
                demo.close()

    def test_update_emotion_granularity_options(self):
        sensevoice_update = gradio_app.update_emotion_granularity_options("sensevoice")
        emotion2vec_update = gradio_app.update_emotion_granularity_options("emotion2vec-plus-large")
        self.assertEqual(sensevoice_update["value"], "utterance")
        self.assertEqual(sensevoice_update["choices"], [("utterance", "utterance")])
        self.assertEqual(
            emotion2vec_update["choices"],
            [("utterance", "utterance"), ("frame", "frame")],
        )


if __name__ == "__main__":
    unittest.main()
