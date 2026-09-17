# -*- coding: utf-8 -*-
"""
程序说明：
测试 server.py 的 TranslateGemma 翻译模型集成（mock transformers，不依赖真实模型）。

目标：
- 验证 translategemma-4b-it 注册进 MODEL_CONFIGS / MODEL_CAPABILITIES。
- 验证加载走 AutoProcessor + AutoModelForImageTextToText（非 NLLB 的 Seq2Seq）。
- 验证本地无缓存时允许联网下载（local_files_only=False）。
- 验证 FLORES → ISO 639-1 语言码映射、chat template 消息结构、generate 参数。
- 验证长文本分块、未知语言码报错、GPU 不可用时回退 CPU。
"""

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

import torch

_ROOT = Path(__file__).resolve().parents[1]
_OPENAI_API_DIR = _ROOT / "app" / "openai_api"
_SERVER_PATH = _OPENAI_API_DIR / "server.py"


def _load_server_module():
    sys.path.insert(0, str(_OPENAI_API_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "funasr_openai_api_server_for_translategemma_tests",
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


class _FakeProcessor:
    """记录 apply_chat_template 调用并返回固定 tensor 输入。"""

    def __init__(self):
        self.apply_calls = []

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=False,
                            return_dict=False, return_tensors=None):
        self.apply_calls.append(messages)
        return {
            "input_ids": torch.tensor([[1, 2, 3, 4]]),
            "attention_mask": torch.tensor([[1, 1, 1, 1]]),
        }

    def decode(self, ids, skip_special_tokens=True):
        return "translated"


class _FakeModel:
    """模拟 Gemma3 模型：to() 可抛异常（测 GPU 回退），generate 返回固定输出。"""

    def __init__(self, fail_gpu=False):
        self.fail_gpu = fail_gpu
        self.generate_kwargs = None

    def to(self, device):
        if self.fail_gpu and str(device).startswith("cuda"):
            raise RuntimeError("CUDA out of memory (mock)")
        return self

    def generate(self, **kwargs):
        self.generate_kwargs = kwargs
        # 输入 4 token，输出 6 token → 去掉输入前缀得到 2 token
        return torch.tensor([[1, 2, 3, 4, 5, 6]])


class TestTranslateGemmaIntegration(unittest.TestCase):
    def setUp(self):
        self.server = _load_server_module()
        self.server.MODEL_REGISTRY.clear()
        self.server._MODEL_LAST_USED.clear()
        self.server.MODEL_LOAD_STATUS.clear()
        self.server.MODEL_LOAD_ERRORS.clear()
        self.processor = _FakeProcessor()
        self.fake_model = _FakeModel()

    def tearDown(self):
        self.server.MODEL_REGISTRY.clear()
        self.server._MODEL_LAST_USED.clear()
        self.server.MODEL_LOAD_STATUS.clear()
        self.server.MODEL_LOAD_ERRORS.clear()
        for key in list(self.server.MODEL_LOAD_EVENTS):
            self.server.MODEL_LOAD_EVENTS.pop(key, None)

    def _load_gemma(self, fail_gpu=False, device=None):
        """mock transformers 后加载 translategemma-4b-it，返回翻译模型实例。"""
        self.fake_model.fail_gpu = fail_gpu
        with mock.patch(
            "transformers.AutoProcessor.from_pretrained",
            return_value=self.processor,
        ), mock.patch(
            "transformers.AutoModelForImageTextToText.from_pretrained",
            return_value=self.fake_model,
        ):
            return self.server.load_model("translategemma-4b-it", device=device)

    def test_registered_in_configs_and_capabilities(self):
        configs = self.server.MODEL_CONFIGS
        self.assertIn("translategemma-4b-it", configs)
        self.assertEqual(configs["translategemma-4b-it"].get("type"), "translation")
        self.assertEqual(
            configs["translategemma-4b-it"].get("model"),
            "google/translategemma-4b-it",
        )
        caps = self.server.MODEL_CAPABILITIES["translategemma-4b-it"]
        self.assertTrue(caps.get("translation"))

    def test_loads_with_processor_and_image_text_model(self):
        """加载走 AutoProcessor + AutoModelForImageTextToText（非 NLLB 的 Seq2Seq）。"""
        with mock.patch(
            "transformers.AutoProcessor.from_pretrained",
            return_value=self.processor,
        ) as proc_patch, mock.patch(
            "transformers.AutoModelForImageTextToText.from_pretrained",
            return_value=self.fake_model,
        ) as model_patch, mock.patch(
            "transformers.AutoModelForSeq2SeqLM.from_pretrained",
            side_effect=AssertionError("NLLB 分支不应被调用"),
        ) as seq2seq_patch:
            model = self.server.load_model("translategemma-4b-it", device="cpu")

        self.assertTrue(hasattr(model, "translate"))
        proc_patch.assert_called_once()
        model_patch.assert_called_once()
        seq2seq_patch.assert_not_called()

    def test_local_cache_absent_allows_download(self):
        """本地无缓存（model_path 未命中）时允许联网下载（local_files_only=False）。"""
        with mock.patch(
            "transformers.AutoProcessor.from_pretrained",
            return_value=self.processor,
        ) as proc_patch, mock.patch(
            "transformers.AutoModelForImageTextToText.from_pretrained",
            return_value=self.fake_model,
        ):
            self.server.load_model("translategemma-4b-it", device="cpu")

        for patch in (proc_patch,):
            _, kwargs = patch.call_args
            self.assertFalse(kwargs.get("local_files_only"))

    def test_translate_maps_language_codes_and_uses_chat_template(self):
        """FLORES 码映射为 ISO 639-1，且走 chat template 消息结构。"""
        model = self._load_gemma()
        result = model.translate("你好", "zho_Hans", "eng_Latn")

        self.assertEqual(result, "translated")
        # 验证 chat template 收到的消息：source_lang_code=zh / target_lang_code=en
        messages = self.processor.apply_calls[-1]
        content = messages[0]["content"][0]
        self.assertEqual(content["type"], "text")
        self.assertEqual(content["source_lang_code"], "zh")
        self.assertEqual(content["target_lang_code"], "en")
        self.assertEqual(content["text"], "你好")
        # generate 参数：greedy 解码 + 足够的新 token 上限
        kwargs = self.fake_model.generate_kwargs
        self.assertFalse(kwargs["do_sample"])
        self.assertGreaterEqual(kwargs["max_new_tokens"], 1024)

    def test_translate_rejects_unknown_language_code(self):
        model = self._load_gemma()
        with self.assertRaises(ValueError):
            model.translate("hi", "xxx_XXX", "eng_Latn")

    def test_translate_chunks_long_text(self):
        """>1200 字文本按句切块，逐块调用并拼接结果。"""
        model = self._load_gemma()
        # 两句各 750 字，合计 1502 字 > 1200 → 切成两块
        long_text = "长" * 750 + "。" + "长" * 750 + "。"
        result = model.translate(long_text, "zho_Hans", "eng_Latn")

        self.assertEqual(len(self.processor.apply_calls), 2)
        self.assertEqual(result, "translated\ntranslated")

    def test_device_fallback_to_cpu_when_gpu_fails(self):
        """CUDA 不可用/显存不足时回退 CPU 并正常返回模型。"""
        model = self._load_gemma(fail_gpu=True, device="cuda")
        self.assertEqual(model.device, "cpu")
        result = model.translate("hi", "eng_Latn", "zho_Hans")
        self.assertEqual(result, "translated")


if __name__ == "__main__":
    unittest.main()
