# -*- coding: utf-8 -*-
"""
程序说明：
测试 "app/openai_api/server.py" 的 "/v1/translations" 端点最小协议。

目标：
- 验证翻译参数校验（非法语言、不支持的模型）。
- 验证成功翻译时的返回格式。
- 保证 nllb 600M 和 1.3B 模型均在模型配置中。
"""

import importlib.util
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
            "funasr_openai_api_server_for_translation_tests",
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


class _DummyTranslationModel:
    def __init__(self, token_response="hello translated"):
        self.token_response = token_response

    def translate(self, text, source_lang, target_lang):
        # 模拟翻译返回
        if isinstance(text, list):
            return [f"[{source_lang}->{target_lang}]: {t}" for t in text]
        return f"[{source_lang}->{target_lang}]: {text}"


class TestServerTranslationEndpoint(unittest.TestCase):
    def setUp(self):
        self.server = _load_server_module()
        self._orig_load_model = self.server.load_model
        self.captured_load_kwargs = None

        def dummy_load_model(model_name: str, **kwargs):
            self.captured_load_kwargs = kwargs
            return _DummyTranslationModel()

        self.server.load_model = dummy_load_model

        from fastapi.testclient import TestClient
        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.server.load_model = self._orig_load_model

    def test_nllb_models_registered_in_configs(self):
        configs = self.server.MODEL_CONFIGS
        self.assertIn("nllb-200-distilled-600m", configs)
        self.assertIn("nllb-200-distilled-1.3b", configs)
        self.assertEqual(configs["nllb-200-distilled-600m"].get("type"), "translation")
        self.assertEqual(configs["nllb-200-distilled-1.3b"].get("type"), "translation")

    def test_translation_rejects_unsupported_model(self):
        resp = self.client.post(
            "/v1/translations",
            json={
                "text": "你好",
                "source_lang": "zho_Hans",
                "target_lang": "eng_Latn",
                "model": "sensevoice",  # ASR model not allowed for translation endpoint
            }
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("not a translation model", resp.json().get("detail", ""))

    def test_translation_rejects_invalid_language(self):
        resp = self.client.post(
            "/v1/translations",
            json={
                "text": "你好",
                "source_lang": "invalid_lang",
                "target_lang": "eng_Latn",
                "model": "nllb-200-distilled-600m",
            }
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("source_lang", resp.json().get("detail", ""))

    def test_translation_success_single_text(self):
        resp = self.client.post(
            "/v1/translations",
            json={
                "text": "你好",
                "source_lang": "zho_Hans",
                "target_lang": "eng_Latn",
                "model": "nllb-200-distilled-600m",
            }
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["translated_text"], "[zho_Hans->eng_Latn]: 你好")

    def test_translation_success_list_text(self):
        resp = self.client.post(
            "/v1/translations",
            json={
                "text": ["你好", "世界"],
                "source_lang": "zho_Hans",
                "target_lang": "eng_Latn",
                "model": "nllb-200-distilled-600m",
            }
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["translated_text"], [
            "[zho_Hans->eng_Latn]: 你好",
            "[zho_Hans->eng_Latn]: 世界"
        ])

    def test_translation_success_extended_language(self):
        # 原 9 种语言中不包含 deu_Latn(德语) 和 spa_Latn(西班牙语)
        # 测试它们在目前被扩充之后能正常在 translations 端点通过校验并被 dummy_model 翻译
        resp = self.client.post(
            "/v1/translations",
            json={
                "text": "Hello",
                "source_lang": "deu_Latn",
                "target_lang": "spa_Latn",
                "model": "nllb-200-distilled-600m",
            }
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["translated_text"], "[deu_Latn->spa_Latn]: Hello")


if __name__ == "__main__":
    unittest.main()
