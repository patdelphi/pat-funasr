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
            },
        )

    def test_build_request_fields_skips_auto_bool_values(self):
        fields = app_utils.build_request_fields(
            model="sensevoice",
            response_format="json",
            merge_vad="",
            use_itn=None,
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
