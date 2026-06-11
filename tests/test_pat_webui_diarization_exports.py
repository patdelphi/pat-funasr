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
import numpy as np
import os
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

    def test_fetch_model_choices_includes_realtime_source_hint(self):
        original_request_json = gradio_app.request_json
        try:
            gradio_app.request_json = lambda *_args, **_kwargs: {
                "data": [{"id": "sensevoice", "ready": True}]
            }
            choices, status_text, payload = gradio_app.fetch_model_choices("http://127.0.0.1:8000", 1)
        finally:
            gradio_app.request_json = original_request_json

        self.assertEqual(choices, [("SenseVoice (sensevoice) [已加载]", "sensevoice")])
        self.assertIn("当前为后端实时模型列表", status_text)
        self.assertEqual(payload["data"][0]["id"], "sensevoice")

    def test_fetch_model_choices_includes_fallback_source_hint(self):
        original_request_json = gradio_app.request_json
        try:
            def raise_error(*_args, **_kwargs):
                raise RuntimeError("boom")

            gradio_app.request_json = raise_error
            choices, status_text, payload = gradio_app.fetch_model_choices("http://127.0.0.1:8000", 1)
        finally:
            gradio_app.request_json = original_request_json

        self.assertGreater(len(choices), 1)
        self.assertIn("当前为静态兜底模型列表", status_text)
        self.assertIn("已回退静态模型清单", status_text)
        self.assertIn("data", payload)

    def test_fetch_model_choices_falls_back_to_static_catalog_when_api_fails(self):
        original_request_json = gradio_app.request_json
        try:
            gradio_app.request_json = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("502"))
            choices, status_text, payload = gradio_app.fetch_model_choices("http://127.0.0.1:8000", 1)
        finally:
            gradio_app.request_json = original_request_json

        values = [value for _, value in choices]
        self.assertIn("sensevoice", values)
        self.assertIn("paraformer", values)
        self.assertIn("emotion2vec-plus-large", values)
        self.assertIn("静态模型清单", status_text)
        self.assertGreater(len(payload["data"]), 3)

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
                batch_size_threshold_s=None,
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

    def test_batch_transcribe_hides_download_until_archive_ready(self):
        temp_file = Path(tempfile.gettempdir()) / "pat-funasr-batch-demo.wav"
        temp_file.write_bytes(b"RIFF....WAVE")
        original_safe_transcribe = gradio_app.safe_transcribe
        try:
            gradio_app.safe_transcribe = lambda **_kwargs: ("ok", '{"text":"ok"}', "mock-result.txt")
            updates = list(
                gradio_app.batch_transcribe(
                    batch_files=[str(temp_file)],
                    base_url="http://127.0.0.1:8000",
                    model="sensevoice",
                    response_format="txt",
                    timeout=1,
                    language="zh",
                    hotword="",
                    vad_preset="",
                    merge_vad="",
                    use_itn="",
                    merge_length_s=15,
                    max_line_width=40,
                    batch_size_s=0,
                    batch_size_threshold_s=0,
                    vad_max_single_segment_time=0,
                    punc_mode="auto",
                    device="",
                    hub="",
                    disable_update="",
                    ncpu=0,
                    log_level="",
                    disable_pbar="true",
                )
            )
        finally:
            gradio_app.safe_transcribe = original_safe_transcribe

        first_download_update = updates[0][1]
        final_download_update = updates[-1][1]
        self.assertEqual(first_download_update["visible"], False)
        self.assertIsNone(first_download_update["value"])
        self.assertEqual(final_download_update["visible"], True)
        self.assertTrue(str(final_download_update["value"]).endswith(".zip"))

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

    def test_format_streaming_preview_text_keeps_single_paragraph(self):
        formatted = gradio_app.format_streaming_preview_text(
            full_text="你好欢迎光临林肯中心今天我们试驾新车。咱们是有预约吗？先生然后这边请",
            final_flag=True,
        )
        self.assertEqual(formatted, "你好欢迎光临林肯中心今天我们试驾新车。咱们是有预约吗？先生然后这边请")

    def test_format_streaming_preview_keeps_very_short_sentences_together(self):
        formatted = gradio_app.format_streaming_preview_text(
            full_text="你好。欢迎。请坐。我们开始。",
            final_flag=False,
        )
        self.assertEqual(formatted, "你好。欢迎。请坐。我们开始。")

    def test_build_streaming_download_file_writes_utf8_bom_text(self):
        output_path = gradio_app.build_streaming_download_file("第一句。\n第二句。")
        try:
            data = Path(output_path).read_bytes()
            self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
            self.assertIn("第一句。", data.decode("utf-8-sig"))
        finally:
            Path(output_path).unlink(missing_ok=True)

    def test_numpy_audio_to_pcm_bytes_downmixes_to_int16(self):
        audio = (8000, np.array([[0.0, 0.5], [1.0, -1.0]], dtype=np.float32))
        pcm = gradio_app.numpy_audio_to_pcm_bytes(audio)
        self.assertIsInstance(pcm, bytes)
        self.assertGreater(len(pcm), 0)
        self.assertEqual(len(pcm) % 2, 0)

    def test_numpy_audio_to_pcm_bytes_preserves_int16_scale(self):
        audio = (16000, np.array([0, 1000, -1000], dtype=np.int16))
        pcm = gradio_app.numpy_audio_to_pcm_bytes(audio)
        samples = np.frombuffer(pcm, dtype=np.int16)

        self.assertEqual(samples.tolist(), [0, 1000, -1000])

    def test_numpy_audio_to_pcm_bytes_preserves_int16_stereo_scale(self):
        audio = (16000, np.array([[1000, -1000], [2000, 2000]], dtype=np.int16))
        pcm = gradio_app.numpy_audio_to_pcm_bytes(audio)
        samples = np.frombuffer(pcm, dtype=np.int16)

        self.assertEqual(samples.tolist(), [0, 2000])

    def test_describe_microphone_signal_reports_peak_level(self):
        status = gradio_app.describe_microphone_signal(
            (16000, np.array([0, 1024, -2048], dtype=np.int16))
        )
        self.assertIn("采样率：16000Hz", status)
        self.assertIn("样本数：3", status)
        self.assertIn("dtype：int16", status)
        self.assertIn("峰值：0.0625", status)
        self.assertIn("已收到有效声音信号", status)

    def test_describe_microphone_signal_treats_float_peak_one_as_loud(self):
        status = gradio_app.describe_microphone_signal(
            (48000, np.array([0.0, 1.0, -1.0], dtype=np.float32))
        )
        self.assertIn("采样率：48000Hz", status)
        self.assertIn("dtype：float32", status)
        self.assertIn("峰值：1.0000", status)
        self.assertIn("已收到有效声音信号", status)
        self.assertNotIn("信号接近静音", status)

    def test_describe_microphone_signal_warns_when_empty(self):
        self.assertIn("没有收到麦克风音频", gradio_app.describe_microphone_signal(None))

    def test_get_default_model_hub_for_ui_reads_environment(self):
        old_value = os.environ.get("FUNASR_MODEL_HUB")
        try:
            os.environ["FUNASR_MODEL_HUB"] = "huggingface"
            self.assertEqual(gradio_app.get_default_model_hub_for_ui(), "hf")
            os.environ["FUNASR_MODEL_HUB"] = "modelscope"
            self.assertEqual(gradio_app.get_default_model_hub_for_ui(), "ms")
        finally:
            if old_value is None:
                os.environ.pop("FUNASR_MODEL_HUB", None)
            else:
                os.environ["FUNASR_MODEL_HUB"] = old_value

    def test_describe_pcm_signal_reports_peak_level(self):
        pcm = np.array([0, 300, -600], dtype=np.int16).tobytes()
        status = gradio_app.describe_pcm_signal(pcm)
        self.assertIn("采样率：16000Hz", status)
        self.assertIn("样本数：3", status)
        self.assertIn("峰值：600", status)

    def test_convert_system_mic_pcm_to_funasr_pcm_resamples_and_downmixes(self):
        source = np.array([[0, 1000], [2000, -2000], [3000, 3000], [-4000, 4000]], dtype=np.int16)
        converted = gradio_app.convert_system_mic_pcm_to_funasr_pcm(
            source.tobytes(),
            source_rate=8000,
            source_channels=2,
        )
        samples = np.frombuffer(converted, dtype=np.int16)
        self.assertGreater(samples.size, source.shape[0])
        self.assertEqual(len(converted) % 2, 0)

    def test_list_system_microphone_device_choices_includes_default_first(self):
        choices = gradio_app.list_system_microphone_device_choices()
        self.assertGreaterEqual(len(choices), 1)
        self.assertEqual(choices[0][1], gradio_app.SYSTEM_MIC_DEFAULT_DEVICE_VALUE)

    def test_get_system_microphone_runtime_status_reports_capture_path(self):
        status = gradio_app.get_system_microphone_runtime_status()
        self.assertIn("Mic 采集链路", status)
        self.assertIn("PCM16/16k", status)

    def test_finish_microphone_streaming_state_reports_sent_chunks(self):
        state, status, transcript = gradio_app.finish_microphone_streaming_state(
            {"sent": 2, "started": True, "full_text": "已有文本。"},
            "http://127.0.0.1:8000",
            "paraformer-zh-streaming",
            1,
            "0,10,5",
            4,
            1,
        )
        self.assertFalse(state["started"])
        self.assertIn("已停止录制", status)
        self.assertIn("已发送分片：2", status)
        self.assertIn("已有文本。", transcript)

    def test_finish_microphone_streaming_state_sends_final_chunk(self):
        original_post_streaming_chunk = gradio_app.post_streaming_chunk
        captured = {}
        try:
            def fake_post_streaming_chunk(**kwargs):
                captured.update(kwargs)
                return {"full_text": "最终文本。"}

            gradio_app.post_streaming_chunk = fake_post_streaming_chunk
            state, status, transcript = gradio_app.finish_microphone_streaming_state(
                {
                    "session_id": "mic-final",
                    "sent": 1,
                    "started": True,
                    "full_text": "",
                    "last_chunk_bytes": b"\x01\x00\x02\x00",
                },
                "http://127.0.0.1:8000",
                "paraformer-zh-streaming",
                1,
                "0,10,5",
                4,
                1,
            )
        finally:
            gradio_app.post_streaming_chunk = original_post_streaming_chunk

        self.assertFalse(state["started"])
        self.assertTrue(captured["is_final"])
        self.assertFalse(captured["reset"])
        self.assertEqual(captured["encoder_chunk_look_back"], 4)
        self.assertEqual(captured["decoder_chunk_look_back"], 1)
        self.assertIn("最终文本。", transcript)
        self.assertIn("最终分片", status)

    def test_system_microphone_capture_updates_session_from_pyaudio_frames(self):
        class FakeStream:
            def __init__(self):
                self.read_count = 0

            def read(self, _frames_per_buffer, exception_on_overflow=False):
                self.read_count += 1
                return np.array([0, 1000, -1000], dtype=np.int16).tobytes()

            def stop_stream(self):
                return None

            def close(self):
                return None

        class FakePyAudioApi:
            opened_kwargs = {}

            def get_default_input_device_info(self):
                return {"index": 0, "name": "Default Fake Mic", "defaultSampleRate": 44100, "maxInputChannels": 2}

            def get_device_info_by_index(self, index):
                return {"index": index, "name": f"Fake Mic {index}", "defaultSampleRate": 48000, "maxInputChannels": 2}

            def open(self, **kwargs):
                FakePyAudioApi.opened_kwargs = kwargs
                return FakeStream()

            def terminate(self):
                return None

        class FakePyAudioModule:
            paInt16 = object()

            @staticmethod
            def PyAudio():
                return FakePyAudioApi()

        original_load_pyaudio_module = gradio_app.load_pyaudio_module
        original_post_streaming_chunk = gradio_app.post_streaming_chunk
        session_id = "test-system-mic"
        stop_event = gradio_app.threading.Event()
        try:
            gradio_app.load_pyaudio_module = lambda: FakePyAudioModule

            def fake_post_streaming_chunk(**_kwargs):
                stop_event.set()
                return {"full_text": "系统麦克风有声音。"}

            gradio_app.post_streaming_chunk = fake_post_streaming_chunk
            with gradio_app.SYSTEM_MIC_STREAMS_LOCK:
                gradio_app.SYSTEM_MIC_STREAMS[session_id] = {
                    "active": True,
                    "stop_event": stop_event,
                    "full_text": "",
                    "status": "",
                    "signal": "",
                    "sent": 0,
                }
            gradio_app.run_system_microphone_capture(
                session_id=session_id,
                base_url="http://127.0.0.1:8000",
                model="paraformer-zh-streaming",
                timeout=1,
                device_value="3",
                chunk_size="0,10,5",
                encoder_chunk_look_back=0,
                decoder_chunk_look_back=0,
            )
            with gradio_app.SYSTEM_MIC_STREAMS_LOCK:
                session = dict(gradio_app.SYSTEM_MIC_STREAMS[session_id])
        finally:
            gradio_app.load_pyaudio_module = original_load_pyaudio_module
            gradio_app.post_streaming_chunk = original_post_streaming_chunk
            with gradio_app.SYSTEM_MIC_STREAMS_LOCK:
                gradio_app.SYSTEM_MIC_STREAMS.pop(session_id, None)

        self.assertFalse(session["active"])
        self.assertEqual(session["sent"], 1)
        self.assertIn("系统麦克风有声音。", session["full_text"])
        self.assertIn("峰值：1000", session["signal"])
        self.assertEqual(FakePyAudioApi.opened_kwargs["input_device_index"], 3)

    def test_stream_transcribe_microphone_yields_frontend_updates(self):
        original_post_streaming_chunk = gradio_app.post_streaming_chunk
        try:
            gradio_app.post_streaming_chunk = lambda **_kwargs: {"full_text": "你好欢迎试驾。请往这边走。"}
            updates = list(
                gradio_app.stream_transcribe_microphone(
                    audio=(16000, np.array([0, 1000, -1000], dtype=np.int16)),
                    state={
                        "session_id": "test-session",
                        "model": "paraformer-zh-streaming",
                        "full_text": "",
                        "last_chunk_bytes": b"",
                        "sent": 0,
                        "started": True,
                        "model_ready": True,
                    },
                    base_url="http://127.0.0.1:8000",
                    model="paraformer-zh-streaming",
                    timeout=1,
                    chunk_size="0,10,5",
                    encoder_chunk_look_back=0,
                    decoder_chunk_look_back=0,
                )
            )
        finally:
            gradio_app.post_streaming_chunk = original_post_streaming_chunk

        self.assertEqual(len(updates), 1)
        transcript, status, state, signal_status = updates[0]
        self.assertIn("你好欢迎试驾。请往这边走。", transcript)
        self.assertIn("已发送分片：1", status)
        self.assertEqual(state["sent"], 1)
        self.assertIn("峰值：0.0305", signal_status)

    def test_init_microphone_streaming_state_waits_for_model_ready(self):
        original_ensure = gradio_app.ensure_streaming_model_ready
        try:
            gradio_app.ensure_streaming_model_ready = lambda *_args, **_kwargs: "模型 paraformer-zh-streaming 已就绪。"
            state, status = gradio_app.init_microphone_streaming_state(
                "http://127.0.0.1:8000",
                "paraformer-zh-streaming",
                1,
            )
        finally:
            gradio_app.ensure_streaming_model_ready = original_ensure

        self.assertTrue(state["model_ready"])
        self.assertTrue(state["started"])
        self.assertIn("已就绪", status)

    def test_stream_transcribe_microphone_skips_when_model_not_ready(self):
        original_post_streaming_chunk = gradio_app.post_streaming_chunk
        calls = []
        try:
            gradio_app.post_streaming_chunk = lambda **kwargs: calls.append(kwargs) or {"full_text": "不应出现"}
            updates = list(
                gradio_app.stream_transcribe_microphone(
                    audio=(16000, np.array([0, 1000, -1000], dtype=np.int16)),
                    state={
                        "session_id": "test-session",
                        "model": "paraformer-zh-streaming",
                        "full_text": "",
                        "sent": 0,
                        "started": False,
                        "model_ready": False,
                        "status": "模型加载失败",
                    },
                    base_url="http://127.0.0.1:8000",
                    model="paraformer-zh-streaming",
                    timeout=1,
                    chunk_size="0,10,5",
                    encoder_chunk_look_back=0,
                    decoder_chunk_look_back=0,
                )
            )
        finally:
            gradio_app.post_streaming_chunk = original_post_streaming_chunk

        self.assertEqual(calls, [])
        self.assertEqual(len(updates), 1)
        self.assertIn("模型加载失败", updates[0][1])

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
            self.assertIn("停止文件识别", button_values)
            self.assertIn("生成结果下载", button_values)
            self.assertIn("生成 Mic 结果下载", button_values)
            self.assertNotIn("开始录制并识别", button_values)
            self.assertIn("timer", component_types)
        finally:
            if hasattr(demo, "close"):
                demo.close()
            close_created_loops(created_loops)

    def test_build_app_separates_offline_single_and_batch_sections(self):
        demo, created_loops = build_demo_with_tracked_loops("http://127.0.0.1:8000", 1)
        try:
            markdown_values = {
                component.get("props", {}).get("value")
                for component in demo.config.get("components", [])
                if component.get("type") == "markdown"
            }
            accordion_labels = {
                component.get("props", {}).get("label")
                for component in demo.config.get("components", [])
                if component.get("type") == "accordion"
            }
            self.assertIn("### 单文件处理", markdown_values)
            self.assertIn("### 批量文件处理", markdown_values)
            self.assertIn("### 文件流式识别", markdown_values)
            self.assertIn("### Mic 实时识别", markdown_values)
            self.assertIn("下载文件", accordion_labels)
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
            self.assertEqual(dropdowns["流式模型"]["value"], "paraformer-zh-streaming")
            self.assertEqual(dropdowns["情感识别模型"]["value"], "emotion2vec-plus-large")
            self.assertEqual(dropdowns["说话人分离模型"]["value"], "paraformer")
        finally:
            if hasattr(demo, "close"):
                demo.close()
            close_created_loops(created_loops)

    def test_build_app_uses_productized_parameter_labels(self):
        demo, created_loops = build_demo_with_tracked_loops("http://127.0.0.1:8000", 1)
        try:
            labels = {
                component.get("props", {}).get("label")
                for component in demo.config.get("components", [])
                if component.get("props", {}).get("label")
            }
            textbox_values = {
                component.get("props", {}).get("label"): component.get("props", {}).get("value")
                for component in demo.config.get("components", [])
                if component.get("type") == "textbox"
            }
            self.assertIn("运行设备(device)", labels)
            self.assertIn("模型来源(hub)", labels)
            self.assertIn("禁用更新检查(disable_update)", labels)
            self.assertIn("CPU 线程数(ncpu)", labels)
            self.assertIn("日志级别(log_level)", labels)
            self.assertIn("禁用进度条(disable_pbar)", labels)
            self.assertIn("分块大小(chunk_size)", labels)
            self.assertEqual(textbox_values["分块大小(chunk_size)"], "0,30,15")
            self.assertIn("编码器回看帧数(encoder_chunk_look_back)", labels)
            self.assertIn("解码器回看帧数(decoder_chunk_look_back)", labels)
            self.assertIn("Gradio 麦克风", labels)
            self.assertIn("麦克风识别状态", labels)
            self.assertIn("麦克风信号", labels)
            self.assertIn("Mic 流式输出", labels)
            self.assertNotIn("麦克风设备", labels)
            html_values = [
                str(component.get("props", {}).get("value", ""))
                for component in demo.config.get("components", [])
                if component.get("type") == "html"
            ]
            self.assertFalse(any("patFormalMicDeviceSelect" in value for value in html_values))
            self.assertFalse(any("patchedFormalGetUserMedia" in value for value in html_values))
            self.assertFalse(any("/mic-stream" in value for value in html_values))
            self.assertFalse(hasattr(gradio_app, "GRADIO_MIC_DEVICE_PICKER_HTML"))
            self.assertFalse(hasattr(gradio_app, "GRADIO_MIC_DEVICE_PICKER_JS"))
            self.assertIn("说话人模型(spk_model)", labels)
            self.assertIn("说话人模式(spk_mode)", labels)
            self.assertIn("预设说话人数(preset_spk_num)", labels)
            self.assertIn("情感粒度(granularity)", labels)
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
