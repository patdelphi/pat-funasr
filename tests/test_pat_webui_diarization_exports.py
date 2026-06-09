"""
程序说明：
测试 "Pat WebUI" 的说话人分离导出文件生成逻辑（unittest）。

目标：
- 验证前端在拿到 diarization JSON 后，能本地生成 json/txt/srt/vtt/tsv/zip 下载文件。
- 验证导出文本中保留 speaker 前缀，避免 UI 下载内容与后端渲染口径不一致。
"""

import importlib.util
import asyncio
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_ROOT = Path(__file__).resolve().parents[1]
_GRADIO_APP_PATH = _ROOT / "app" / "pat_funasr_webui" / "gradio_app.py"
_SPEC = importlib.util.spec_from_file_location("funasr_pat_gradio_app", _GRADIO_APP_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"无法加载 Pat WebUI 模块：{_GRADIO_APP_PATH}")
gradio_app = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gradio_app)


def cleanup_asyncio_event_loop() -> None:
    """清理当前测试线程残留的事件循环，避免 Windows 下的未关闭告警。"""
    try:
        current_loop = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        current_loop = None
    if current_loop is not None and not current_loop.is_closed():
        current_loop.close()
    asyncio.set_event_loop(None)


def build_demo_with_tracked_loops(*args, **kwargs):
    """构建 Gradio demo，并记录过程中创建的事件循环，便于测试后显式关闭。"""
    created_loops = []
    original_new_event_loop = asyncio.new_event_loop

    def tracked_new_event_loop():
        loop = original_new_event_loop()
        created_loops.append(loop)
        return loop

    asyncio.new_event_loop = tracked_new_event_loop
    try:
        demo = gradio_app.build_app(*args, **kwargs)
    finally:
        asyncio.new_event_loop = original_new_event_loop
    return demo, created_loops


def close_created_loops(created_loops) -> None:
    """关闭测试期间创建的事件循环，避免资源告警。"""
    for loop in reversed(created_loops):
        if loop is not None and not loop.is_closed():
            loop.close()


class TestPatWebUiDiarizationExports(unittest.TestCase):
    def tearDown(self):
        cleanup_asyncio_event_loop()

    def test_initialize_service_dashboard_returns_updates(self):
        original_fetch_model_choices = gradio_app.fetch_model_choices
        original_request_json = gradio_app.request_json
        original_read_runtime_logs = gradio_app.read_runtime_logs
        try:
            gradio_app.fetch_model_choices = lambda *_args, **_kwargs: (
                [("SenseVoice (sensevoice) [已加载]", "sensevoice")],
                "mock-status",
                {"data": [{"id": "sensevoice", "ready": True}]},
            )
            gradio_app.request_json = lambda *_args, **_kwargs: {"status": "ok"}
            gradio_app.read_runtime_logs = lambda *_args, **_kwargs: "mock-logs"

            outputs = gradio_app.initialize_service_dashboard("http://127.0.0.1:8000", 1, "all")
        finally:
            gradio_app.fetch_model_choices = original_fetch_model_choices
            gradio_app.request_json = original_request_json
            gradio_app.read_runtime_logs = original_read_runtime_logs

        self.assertEqual(len(outputs), 10)
        self.assertEqual(outputs[4], "mock-status")
        self.assertIn('"status": "ok"', outputs[5])
        self.assertIn("### 运行概览", outputs[6])
        self.assertIn("### 模型能力看板", outputs[7])
        self.assertIn("### 使用建议", outputs[8])
        self.assertEqual(outputs[9], "mock-logs")

    def test_request_json_disables_timeout_when_non_positive(self):
        calls = []
        original_urlopen = gradio_app.urllib.request.urlopen

        class _DummyResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b"{}"

        try:
            def fake_urlopen(request, *args, **kwargs):
                calls.append({"args": args, "kwargs": kwargs})
                return _DummyResponse()

            gradio_app.urllib.request.urlopen = fake_urlopen
            payload = gradio_app.request_json("http://127.0.0.1:8000/health", 0)
        finally:
            gradio_app.urllib.request.urlopen = original_urlopen

        self.assertEqual(payload, {})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["kwargs"], {})

    def test_read_runtime_logs_reads_api_and_ui_logs(self):
        temp_root = Path(tempfile.mkdtemp(prefix="pat-funasr-log-test-"))
        api_log = temp_root / "funasr-api.log"
        ui_log = temp_root / "funasr-ui.log"
        api_log.write_text("api-1\napi-2\napi-3\n", encoding="utf-8")
        ui_log.write_text("ui-1\nui-2\n", encoding="utf-8")

        original_project_root = gradio_app.PROJECT_ROOT
        try:
            gradio_app.PROJECT_ROOT = temp_root
            content = gradio_app.read_runtime_logs(max_lines=2)
        finally:
            gradio_app.PROJECT_ROOT = original_project_root

        self.assertIn("=== funasr-api.log ===", content)
        self.assertIn("api-2", content)
        self.assertIn("api-3", content)
        self.assertNotIn("api-1", content)
        self.assertIn("=== funasr-ui.log ===", content)
        self.assertIn("ui-1", content)
        self.assertIn("ui-2", content)

    def test_read_runtime_logs_ui_initializes_tick_counter(self):
        temp_root = Path(tempfile.mkdtemp(prefix="pat-funasr-log-ui-test-"))
        ui_log = temp_root / "funasr-ui.log"
        ui_log.write_text("ui-line-1\nui-line-2\n", encoding="utf-8")

        original_project_root = gradio_app.PROJECT_ROOT
        had_counter = hasattr(gradio_app, "_RUNTIME_LOG_TICK_COUNTER")
        original_counter = getattr(gradio_app, "_RUNTIME_LOG_TICK_COUNTER", None)
        try:
            gradio_app.PROJECT_ROOT = temp_root
            if hasattr(gradio_app, "_RUNTIME_LOG_TICK_COUNTER"):
                delattr(gradio_app, "_RUNTIME_LOG_TICK_COUNTER")
            content = gradio_app.read_runtime_logs_ui(10, 64, 2000)
            counter_value = getattr(gradio_app, "_RUNTIME_LOG_TICK_COUNTER", None)
        finally:
            gradio_app.PROJECT_ROOT = original_project_root
            if had_counter:
                gradio_app._RUNTIME_LOG_TICK_COUNTER = original_counter
            elif hasattr(gradio_app, "_RUNTIME_LOG_TICK_COUNTER"):
                delattr(gradio_app, "_RUNTIME_LOG_TICK_COUNTER")

        self.assertIn("ui-line-1", content)
        self.assertEqual(counter_value, 1)

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

    def test_transcribe_audio_with_exports_supports_all_preview_formats(self):
        payload = {
            "text": "你好，欢迎光临。",
            "segments": [
                {"start": 0.0, "end": 1.2, "text": "你好，", "speaker": None},
                {"start": 1.2, "end": 2.8, "text": "欢迎光临。", "speaker": None},
            ],
            "language": "zh",
        }
        original_request_transcription_payload = gradio_app.request_transcription_payload
        try:
            gradio_app.request_transcription_payload = lambda **_kwargs: payload
            preview_text, preview_state_json, *_downloads = gradio_app.transcribe_audio_with_exports(
                base_url="http://127.0.0.1:8000",
                audio_path=r"y:\NewStore\AI\FunASR-Portable-GPU\test\demo.wav",
                model="paraformer",
                response_format="txt",
                timeout=0,
            )
        finally:
            gradio_app.request_transcription_payload = original_request_transcription_payload

        self.assertIn("你好，", preview_text)
        preview_srt = gradio_app.update_transcription_preview("srt", preview_state_json)
        preview_vtt = gradio_app.update_transcription_preview("vtt", preview_state_json)
        preview_tsv = gradio_app.update_transcription_preview("tsv", preview_state_json)
        self.assertIn("00:00:00,000 --> 00:00:01,200", preview_srt)
        self.assertIn("00:00:00.000 --> 00:00:01.200", preview_vtt)
        self.assertIn("0.00\t1.20\t你好，", preview_tsv)

    def test_safe_transcribe_with_exports_large_file_still_returns_full_preview(self):
        temp_file = Path(tempfile.mkdtemp(prefix="pat-funasr-large-preview-")) / "large.wav"
        temp_file.write_bytes(b"0" * (26 * 1024 * 1024))
        expected = (
            "完整预览内容",
            '{"exports": {"txt": "mock.txt"}}',
            "mock.json",
            "mock.txt",
            "mock.srt",
            "mock.vtt",
            "mock.tsv",
            "mock.zip",
        )
        original_transcribe_audio_with_exports = gradio_app.transcribe_audio_with_exports
        try:
            gradio_app.transcribe_audio_with_exports = lambda **_kwargs: expected
            result = gradio_app.safe_transcribe_with_exports(
                base_url="http://127.0.0.1:8000",
                audio_path=str(temp_file),
                model="paraformer",
                preview_format="txt",
                timeout=0,
                language=None,
                hotword=None,
                vad_preset=None,
                merge_vad=None,
                use_itn=None,
                merge_length_s=None,
                max_line_width=None,
                batch_size_s=None,
                vad_max_single_segment_time=None,
                punc_mode=None,
                device=None,
                hub=None,
                disable_update=None,
                ncpu=None,
                log_level=None,
                disable_pbar=None,
            )
        finally:
            gradio_app.transcribe_audio_with_exports = original_transcribe_audio_with_exports

        self.assertEqual(result, expected)
        self.assertNotIn("省内存模式", result[0])

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

    def test_auto_refresh_service_dashboard_guard_returns_updates(self):
        original_fetch_model_choices = gradio_app.fetch_model_choices
        original_request_json = gradio_app.request_json
        original_read_runtime_logs_ui = gradio_app.read_runtime_logs_ui
        try:
            gradio_app.fetch_model_choices = lambda *_args, **_kwargs: (
                [("SenseVoice (sensevoice) [已加载]", "sensevoice")],
                "mock-status",
                {"data": [{"id": "sensevoice", "ready": True}]},
            )
            gradio_app.request_json = lambda *_args, **_kwargs: {"status": "ok"}
            gradio_app.read_runtime_logs_ui = lambda *_args, **_kwargs: "mock-logs"

            outputs = gradio_app.auto_refresh_service_dashboard_guard(
                True,
                True,
                "http://127.0.0.1:8000",
                1,
                "all",
                120,
                256,
                8000,
            )
        finally:
            gradio_app.fetch_model_choices = original_fetch_model_choices
            gradio_app.request_json = original_request_json
            gradio_app.read_runtime_logs_ui = original_read_runtime_logs_ui

        self.assertEqual(len(outputs), 6)
        self.assertEqual(outputs[0], "mock-status")
        self.assertIn('"status": "ok"', outputs[1])
        self.assertIn("### 运行概览", outputs[2])
        self.assertIn("### 模型能力看板", outputs[3])
        self.assertIn("### 使用建议", outputs[4])
        self.assertEqual(outputs[5], "mock-logs")

    def test_auto_refresh_service_dashboard_guard_skips_when_tab_inactive(self):
        outputs = gradio_app.auto_refresh_service_dashboard_guard(
            True,
            False,
            "http://127.0.0.1:8000",
            1,
            "all",
            120,
            256,
            8000,
        )
        self.assertEqual(len(outputs), 6)
        for item in outputs:
            self.assertIsInstance(item, dict)

    def test_activate_and_refresh_service_tab_returns_immediate_updates(self):
        original_fetch_model_choices = gradio_app.fetch_model_choices
        original_request_json = gradio_app.request_json
        original_read_runtime_logs_ui = gradio_app.read_runtime_logs_ui
        try:
            gradio_app.fetch_model_choices = lambda *_args, **_kwargs: (
                [("SenseVoice (sensevoice) [已加载]", "sensevoice")],
                "mock-status",
                {"data": [{"id": "sensevoice", "ready": True}]},
            )
            gradio_app.request_json = lambda *_args, **_kwargs: {"status": "ok"}
            gradio_app.read_runtime_logs_ui = lambda *_args, **_kwargs: "mock-logs"

            outputs = gradio_app.activate_and_refresh_service_tab(
                "http://127.0.0.1:8000",
                1,
                "all",
                120,
                256,
                8000,
            )
        finally:
            gradio_app.fetch_model_choices = original_fetch_model_choices
            gradio_app.request_json = original_request_json
            gradio_app.read_runtime_logs_ui = original_read_runtime_logs_ui

        self.assertEqual(len(outputs), 7)
        self.assertTrue(outputs[0])
        self.assertEqual(outputs[1], "mock-status")
        self.assertIn('"status": "ok"', outputs[2])
        self.assertIn("### 运行概览", outputs[3])
        self.assertIn("### 模型能力看板", outputs[4])
        self.assertIn("### 使用建议", outputs[5])
        self.assertEqual(outputs[6], "mock-logs")

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
        demo, created_loops = build_demo_with_tracked_loops("http://127.0.0.1:8000", 1)
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
            component_types = {
                component.get("type")
                for component in demo.config.get("components", [])
            }
            api_names = {
                dependency.get("api_name")
                for dependency in demo.config.get("dependencies", [])
            }
            self.assertIn("模型摘要", labels)
            self.assertIn("运行日志", labels)
            self.assertIn("刷新模型列表", button_values)
            self.assertIn("检查服务", button_values)
            self.assertIn("刷新运行日志", button_values)
            self.assertIn("timer", component_types)
        finally:
            if hasattr(demo, "close"):
                demo.close()
            close_created_loops(created_loops)

    def test_build_app_falls_back_for_function_model_dropdowns(self):
        original_fetch_model_choices = gradio_app.fetch_model_choices
        try:
            gradio_app.fetch_model_choices = lambda *_args, **_kwargs: (
                [("Qwen3-ASR-1.7B (qwen3-asr) [已加载]", "qwen3-asr")],
                "mock",
            )
            demo, created_loops = build_demo_with_tracked_loops("http://127.0.0.1:8000", 1)
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
            close_created_loops(created_loops)

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
