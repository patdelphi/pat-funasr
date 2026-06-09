"""
程序说明：
Pat WebUI 辅助模块单元测试（unittest）。

目标：
- 校验模型列表解析逻辑，避免 UI 与 "/v1/models" 返回结构脱节。
- 校验请求字段白名单，只提交后端当前支持的参数。
- 校验输出格式与下载文件名映射，降低后续下载逻辑回归风险。
"""

import importlib.util
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_UTILS_PATH = _ROOT / "app" / "pat_funasr_webui" / "app_utils.py"
_SPEC = importlib.util.spec_from_file_location("funasr_pat_webui_utils", _UTILS_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"无法加载 Pat WebUI 辅助模块：{_UTILS_PATH}")
app_utils = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(app_utils)


class TestPatWebUiUtils(unittest.TestCase):
    def test_parse_model_choices_keeps_ready_state(self):
        payload = {
            "object": "list",
            "data": [
                {"id": "sensevoice", "ready": True},
                {"id": "fun-asr-nano", "ready": False},
            ],
        }

        choices = app_utils.parse_model_choices(payload)

        self.assertEqual(
            choices,
            [
                ("SenseVoice 多语言 (sensevoice) [ready]", "sensevoice"),
                ("Fun-ASR-Nano (fun-asr-nano) [lazy-load]", "fun-asr-nano"),
            ],
        )

    def test_choose_default_model_prefers_sensevoice(self):
        choices = [
            ("Fun-ASR-Nano (fun-asr-nano) [lazy-load]", "fun-asr-nano"),
            ("SenseVoice 多语言 (sensevoice) [ready]", "sensevoice"),
        ]
        self.assertEqual(app_utils.choose_default_model(choices), "sensevoice")

    def test_filter_streaming_model_choices(self):
        choices = [
            ("SenseVoice 多语言 (sensevoice) [ready]", "sensevoice"),
            ("Paraformer Streaming 中文 (paraformer-zh-streaming) [lazy-load]", "paraformer-zh-streaming"),
            ("Qwen3-ASR-0.6B (qwen3-asr-0.6b) [lazy-load]", "qwen3-asr-0.6b"),
        ]
        filtered = app_utils.filter_streaming_model_choices(choices)
        self.assertEqual(
            filtered,
            [("Paraformer Streaming 中文 (paraformer-zh-streaming) [lazy-load]", "paraformer-zh-streaming")],
        )
        self.assertEqual(
            app_utils.choose_default_streaming_model(filtered),
            "paraformer-zh-streaming",
        )

    def test_ensure_dropdown_choices_falls_back_to_default(self):
        ensured = app_utils.ensure_dropdown_choices([], fallback="paraformer-zh-streaming")
        self.assertEqual(
            ensured,
            [("Paraformer Streaming 中文 (paraformer-zh-streaming) [lazy-load]", "paraformer-zh-streaming")],
        )

    def test_filter_emotion_model_choices(self):
        choices = [
            ("SenseVoice 多语言 (sensevoice) [ready]", "sensevoice"),
            ("Emotion2Vec Plus Large (emotion2vec-plus-large) [lazy-load]", "emotion2vec-plus-large"),
            ("Qwen3-ASR-0.6B (qwen3-asr-0.6b) [lazy-load]", "qwen3-asr-0.6b"),
        ]
        filtered = app_utils.filter_emotion_model_choices(choices)
        self.assertEqual(
            filtered,
            [
                ("SenseVoice 多语言 (sensevoice) [ready]", "sensevoice"),
                ("Emotion2Vec Plus Large (emotion2vec-plus-large) [lazy-load]", "emotion2vec-plus-large"),
            ],
        )
        self.assertEqual(
            app_utils.choose_default_emotion_model(filtered),
            "emotion2vec-plus-large",
        )

    def test_filter_diarization_model_choices(self):
        choices = [
            ("Paraformer 中文 (paraformer) [ready]", "paraformer"),
            ("SenseVoice 多语言 (sensevoice) [ready]", "sensevoice"),
            ("Fun-ASR-Nano (fun-asr-nano) [lazy-load]", "fun-asr-nano"),
            ("Emotion2Vec Plus Large (emotion2vec-plus-large) [lazy-load]", "emotion2vec-plus-large"),
        ]
        filtered = app_utils.filter_diarization_model_choices(choices)
        self.assertEqual(
            filtered,
            [
                ("Paraformer 中文 (paraformer) [ready]", "paraformer"),
                ("SenseVoice 多语言 (sensevoice) [ready]", "sensevoice"),
                ("Fun-ASR-Nano (fun-asr-nano) [lazy-load]", "fun-asr-nano"),
            ],
        )
        self.assertEqual(
            app_utils.choose_default_diarization_model(filtered),
            "paraformer",
        )

    def test_media_file_suffixes_cover_audio_and_video(self):
        self.assertIn(".wav", app_utils.MEDIA_FILE_SUFFIXES)
        self.assertIn(".mp4", app_utils.MEDIA_FILE_SUFFIXES)

    def test_is_video_file(self):
        self.assertTrue(app_utils.is_video_file("demo.mp4"))
        self.assertTrue(app_utils.is_video_file(Path("demo.mkv")))
        self.assertFalse(app_utils.is_video_file("demo.wav"))
        self.assertFalse(app_utils.is_video_file(None))

    def test_render_model_capability_markdown(self):
        payload = {
            "data": [
                {
                    "id": "sensevoice",
                    "ready": True,
                    "capabilities": {
                        "offline_asr": True,
                        "streaming_asr": False,
                        "diarization": False,
                        "emotion": True,
                        "vad": True,
                        "punc": True,
                        "notes": "后端返回能力",
                    },
                },
                {
                    "id": "paraformer-zh-streaming",
                    "ready": False,
                    "capabilities": {
                        "offline_asr": False,
                        "streaming_asr": True,
                        "diarization": False,
                        "emotion": False,
                        "vad": False,
                        "punc": False,
                        "notes": "流式专用",
                    },
                },
            ]
        }
        markdown = app_utils.render_model_capability_markdown(payload)
        self.assertIn("### 模型能力看板", markdown)
        self.assertIn("SenseVoice 多语言 (`sensevoice`)", markdown)
        self.assertIn("Paraformer Streaming 中文 (`paraformer-zh-streaming`)", markdown)
        self.assertIn("| ready |", markdown)
        self.assertIn("| lazy-load |", markdown)
        self.assertIn("后端返回能力", markdown)
        self.assertIn("流式专用", markdown)
        self.assertIn("当前筛选：`全部模型`", markdown)

    def test_filter_model_capability_rows(self):
        rows = [
            {
                "model": "sensevoice",
                "label": "SenseVoice 多语言",
                "ready": "ready",
                "offline_asr": "Y",
                "streaming_asr": "-",
                "diarization": "-",
                "emotion": "Y",
                "vad": "Y",
                "punc": "Y",
                "notes": "",
            },
            {
                "model": "paraformer-zh-streaming",
                "label": "Paraformer Streaming 中文",
                "ready": "lazy-load",
                "offline_asr": "-",
                "streaming_asr": "Y",
                "diarization": "-",
                "emotion": "-",
                "vad": "-",
                "punc": "-",
                "notes": "",
            },
        ]
        filtered = app_utils.filter_model_capability_rows(rows, "streaming_asr")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["model"], "paraformer-zh-streaming")

    def test_render_capability_target_markdown(self):
        payload = {
            "data": [
                {
                    "id": "paraformer-zh-streaming",
                    "ready": False,
                    "capabilities": {
                        "offline_asr": False,
                        "streaming_asr": True,
                        "diarization": False,
                        "emotion": False,
                        "vad": False,
                        "punc": False,
                        "notes": "流式专用",
                    },
                }
            ]
        }
        markdown = app_utils.render_capability_target_markdown(payload, "streaming_asr")
        self.assertIn("建议页面：`流式识别`", markdown)
        self.assertIn("重点区域：`Streaming 模型 + chunk_size`", markdown)
        self.assertIn("`paraformer-zh-streaming`", markdown)

    def test_build_request_fields_filters_non_whitelist(self):
        fields = app_utils.build_request_fields(
            model="sensevoice",
            response_format="srt",
            language="zh",
            vad_preset="default",
            merge_vad="true",
            merge_length_s=15,
            max_line_width=40,
            hotword="项目名,专有词",
            use_itn="false",
            batch_size_s=30,
            vad_max_single_segment_time=15000,
            punc_mode="disabled",
            device="cuda",
            hub="ms",
            disable_update="true",
            ncpu=4,
            log_level="DEBUG",
            disable_pbar="false",
        )

        self.assertEqual(
            fields,
            {
                "model": "sensevoice",
                "response_format": "srt",
                "language": "zh",
                "vad_preset": "default",
                "merge_vad": "true",
                "merge_length_s": "15",
                "max_line_width": "40",
                "hotword": "项目名,专有词",
                "use_itn": "false",
                "batch_size_s": "30",
                "vad_max_single_segment_time": "15000",
                "punc_mode": "disabled",
                "device": "cuda",
                "hub": "ms",
                "disable_update": "true",
                "ncpu": "4",
                "log_level": "DEBUG",
                "disable_pbar": "false",
            },
        )

    def test_build_request_fields_skips_auto_bool_values(self):
        fields = app_utils.build_request_fields(
            model="sensevoice",
            response_format="json",
            merge_vad="",
            use_itn=None,
            disable_update="",
            disable_pbar=None,
        )
        self.assertEqual(
            fields,
            {
                "model": "sensevoice",
                "response_format": "json",
            },
        )

    def test_output_filename_for_format(self):
        self.assertEqual(app_utils.output_filename_for_format("txt"), "output.txt")
        self.assertEqual(app_utils.output_filename_for_format("all"), "output.zip")

    def test_normalize_uploaded_paths(self):
        paths = app_utils.normalize_uploaded_paths(
            [
                "a.wav",
                Path("b.wav"),
                {"path": "c.wav"},
                {"name": "d.wav"},
            ]
        )
        self.assertEqual(paths, ["a.wav", "b.wav", "c.wav", "d.wav"])

    def test_summarize_batch_results(self):
        summary = app_utils.summarize_batch_results(
            [
                {"file_name": "a.wav", "status": "success", "ok": True},
                {"file_name": "b.wav", "status": "running"},
                {"file_name": "c.wav", "status": "pending"},
                {"file_name": "d.wav", "status": "error", "ok": False, "message": "HTTP 500"},
            ]
        )
        self.assertIn("总计：4", summary)
        self.assertIn("进度：2/4", summary)
        self.assertIn("[OK] a.wav", summary)
        self.assertIn("[RUN] b.wav", summary)
        self.assertIn("[TODO] c.wav", summary)
        self.assertIn("[ERR] d.wav -> HTTP 500", summary)

    def test_initialize_batch_results(self):
        results = app_utils.initialize_batch_results(["a.wav", "folder/b.wav"])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["file_name"], "a.wav")
        self.assertEqual(results[0]["status"], "pending")
        self.assertEqual(results[1]["file_name"], "b.wav")


if __name__ == "__main__":
    unittest.main()
