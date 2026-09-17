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


class TestTranslationLoadsFromLocalCache(unittest.TestCase):
    """验证 translation 分支从本地缓存（model_path）加载，而非原始模型 ID。

    回归：修复前 `model_dir = model_id` 忽略了 _resolve_runtime_models_to_local
    写入的 model_path，导致 transformers 尝试联网下载而 500。
    """

    def setUp(self):
        self.server = _load_server_module()
        self.server.MODEL_REGISTRY.clear()
        self.server._MODEL_LAST_USED.clear()
        self.server.MODEL_LOAD_STATUS.clear()
        self.server.MODEL_LOAD_ERRORS.clear()

    def tearDown(self):
        self.server.MODEL_REGISTRY.clear()
        self.server._MODEL_LAST_USED.clear()
        self.server.MODEL_LOAD_STATUS.clear()
        self.server.MODEL_LOAD_ERRORS.clear()
        for key in list(self.server.MODEL_LOAD_EVENTS):
            self.server.MODEL_LOAD_EVENTS.pop(key, None)

    def test_translation_branch_uses_resolved_local_path(self):
        from unittest import mock

        calls = []

        class _FakeModel:
            pass

        def fake_tokenizer_from_pretrained(path, *args, **kwargs):
            calls.append(("tokenizer", str(path)))

            class _Tok:
                src_lang = "zho_Hans"

                def convert_tokens_to_ids(self, token):
                    return 1

                def __call__(self, text, **kwargs):
                    return {"input_ids": [[1, 2]], "attention_mask": [[1, 1]]}

                def batch_decode(self, outputs, **kwargs):
                    return ["hello"]

            return _Tok()

        def fake_model_from_pretrained(path, *args, **kwargs):
            calls.append(("model", str(path)))
            return _FakeModel()

        with mock.patch(
            "transformers.AutoTokenizer.from_pretrained",
            side_effect=fake_tokenizer_from_pretrained,
        ), mock.patch(
            "transformers.AutoModelForSeq2SeqLM.from_pretrained",
            side_effect=fake_model_from_pretrained,
        ):
            model = self.server.load_model("nllb-200-distilled-600m")

        # 两次 from_pretrained 都必须拿到本地缓存绝对路径，而非原始模型 ID
        self.assertEqual(len(calls), 2)
        for kind, path in calls:
            self.assertIn(".cache", path, msg=f"{kind} 未使用本地缓存路径: {path}")
            self.assertFalse(
                path.startswith("facebook/nllb"),
                msg=f"{kind} 仍使用原始模型 ID: {path}",
            )
        self.assertTrue(hasattr(model, "translate"))


class TestNllbFp16AndTranslationUnload(unittest.TestCase):
    """验证 A+B 修复：

    - A: NLLB 在 GPU 上以 fp16 加载（显存减半）。
    - B: 切换翻译模型时自动卸载其他翻译模型，释放显存。
    """

    def setUp(self):
        self.server = _load_server_module()
        self.server.MODEL_REGISTRY.clear()
        self.server._MODEL_LAST_USED.clear()
        self.server.MODEL_LOAD_STATUS.clear()
        self.server.MODEL_LOAD_ERRORS.clear()

    def tearDown(self):
        self.server.MODEL_REGISTRY.clear()
        self.server._MODEL_LAST_USED.clear()
        self.server.MODEL_LOAD_STATUS.clear()
        self.server.MODEL_LOAD_ERRORS.clear()
        for key in list(self.server.MODEL_LOAD_EVENTS):
            self.server.MODEL_LOAD_EVENTS.pop(key, None)

    def _patch_nllb(self, fake_model):
        """mock transformers，返回 fake_model 作为 Seq2Seq 模型。"""
        from unittest import mock
        return mock.patch(
            "transformers.AutoTokenizer.from_pretrained",
            side_effect=lambda *a, **k: object(),
        ), mock.patch(
            "transformers.AutoModelForSeq2SeqLM.from_pretrained",
            return_value=fake_model,
        )

    def test_nllb_gpu_load_uses_half(self):
        """GPU 加载 NLLB 时模型转 fp16 并加载到指定设备。"""
        from unittest import mock

        class _FakeSeq2Seq:
            def __init__(self):
                self.half_called = 0
                self.to_device = None

            def half(self):
                self.half_called += 1
                return self

            def to(self, device):
                self.to_device = device
                return self

        fake_model = _FakeSeq2Seq()
        p1, p2 = self._patch_nllb(fake_model)
        with p1, p2:
            model = self.server.load_model("nllb-200-distilled-600m", device="cuda")

        self.assertTrue(hasattr(model, "translate"))
        self.assertEqual(fake_model.half_called, 1)
        self.assertEqual(fake_model.to_device, "cuda")

    def test_switching_translation_model_unloads_previous(self):
        """切换翻译模型时，先前驻留的翻译模型被卸载。"""
        from unittest import mock

        p1, p2 = self._patch_nllb(_FakeNllbWeight())
        with p1, p2:
            model_600m = self.server.load_model("nllb-200-distilled-600m", device="cuda")
        self.assertTrue(hasattr(model_600m, "translate"))

        # 加载 1.3B 前 600M 驻留
        self.assertTrue(
            any(k.startswith("nllb-200-distilled-600m") for k in self.server.MODEL_REGISTRY)
        )

        with p1, p2:
            model_1_3b = self.server.load_model("nllb-200-distilled-1.3b", device="cuda")

        self.assertTrue(hasattr(model_1_3b, "translate"))
        self.assertFalse(
            any(k.startswith("nllb-200-distilled-600m") for k in self.server.MODEL_REGISTRY),
            msg="600M 未被卸载",
        )
        self.assertTrue(
            any(k.startswith("nllb-200-distilled-1.3b") for k in self.server.MODEL_REGISTRY)
        )
        self.assertEqual(
            self.server.MODEL_LOAD_STATUS["nllb-200-distilled-600m"]["state"], "unloaded"
        )

    def test_unload_calls_gguf_close_and_keeps_funasr(self):
        """切换翻译模型时卸载 GGUF（调 llama close），但保留 FunASR（避免打断并发转录）。"""
        from unittest import mock

        class _FakeLlamaWithClose:
            def __init__(self):
                self.closed = 0

            def close(self):
                self.closed += 1

        class _FakeGgufModel:
            def __init__(self):
                self.llm = _FakeLlamaWithClose()

            def translate(self, *a, **k):
                return ""

        class _FakeFunasrModel:
            def transcribe(self, *a, **k):
                return {}

        gguf_model = _FakeGgufModel()
        self.server.MODEL_REGISTRY["translategemma-4b-it-gguf::device=cuda"] = gguf_model
        self.server.MODEL_REGISTRY["sensevoice::device=cuda"] = _FakeFunasrModel()
        self.server.MODEL_LOAD_STATUS["translategemma-4b-it-gguf"] = {
            "state": "ready", "error": None, "updated_at": 0.0,
        }
        self.server.MODEL_LOAD_STATUS["sensevoice"] = {
            "state": "ready", "error": None, "updated_at": 0.0,
        }

        p1, p2 = self._patch_nllb(_FakeNllbWeight())
        with p1, p2:
            self.server.load_model("nllb-200-distilled-600m", device="cuda")

        self.assertEqual(gguf_model.llm.closed, 1, "GGUF llama close 未被调用")
        self.assertNotIn("translategemma-4b-it-gguf::device=cuda", self.server.MODEL_REGISTRY)
        self.assertIn("sensevoice::device=cuda", self.server.MODEL_REGISTRY, "FunASR 应保留")
        self.assertEqual(
            self.server.MODEL_LOAD_STATUS["translategemma-4b-it-gguf"]["state"], "unloaded"
        )
        self.assertEqual(self.server.MODEL_LOAD_STATUS["sensevoice"]["state"], "ready")

    def test_nllb_gpu_load_failure_falls_back_to_cpu(self):
        """NLLB GPU 加载失败（CUDA 上下文异常）时回退 CPU，不抛 500。"""
        from unittest import mock

        class _FakeSeq2Seq:
            def half(self):
                raise RuntimeError("CUDA error: invalid argument (mock)")

            def to(self, device):
                return self

        p1, p2 = self._patch_nllb(_FakeSeq2Seq())
        with p1, p2:
            model = self.server.load_model("nllb-200-distilled-1.3b", device="cuda")

        self.assertTrue(hasattr(model, "translate"))
        self.assertEqual(model.device, "cpu")

    def test_unload_keeps_requested_model(self):
        """卸载逻辑保留当前请求的模型。"""
        class _FakeGgufModel:
            def __init__(self):
                self.llm = _FakeLlama()
            def translate(self, *a, **k):
                return ""

        class _FakeLlama:
            def close(self):
                pass

        self.server.MODEL_REGISTRY["nllb-200-distilled-600m::device=cuda"] = _FakeGgufModel()
        self.server._unload_translation_models_except("nllb-200-distilled-600m")
        self.assertIn("nllb-200-distilled-600m::device=cuda", self.server.MODEL_REGISTRY)


class _FakeNllbWeight:
    """NLLB 权重假对象：支持 .half()/.to()。"""

    def half(self):
        return self

    def to(self, device):
        return self


if __name__ == "__main__":
    unittest.main()
