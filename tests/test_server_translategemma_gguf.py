# -*- coding: utf-8 -*-
"""
程序说明：
测试 server.py 的 TranslateGemma GGUF 翻译模型集成（mock llama_cpp，不依赖真实推理）。

目标：
- 验证 translategemma-4b-it-gguf 注册进 MODEL_CONFIGS / MODEL_CAPABILITIES（format=gguf）。
- 验证加载走 llama_cpp.Llama（n_ctx=2048 / n_gpu_layers=-1 优先 GPU）。
- 验证本地缓存目录中定位 .gguf 权重（排除 mmproj）。
- 验证 Gemma3 prompt 构造、FLORES→ISO 语言码映射、长文本分块、未知码报错、GPU 回退 CPU。
"""

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
_OPENAI_API_DIR = _ROOT / "app" / "openai_api"
_SERVER_PATH = _OPENAI_API_DIR / "server.py"


def _load_server_module():
    sys.path.insert(0, str(_OPENAI_API_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "funasr_openai_api_server_for_gguf_tests",
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


class _FakeLlama:
    """模拟 llama_cpp.Llama：记录构造参数与调用，返回固定翻译结果。"""

    instances = []

    def __init__(self, model_path, n_ctx=2048, n_gpu_layers=0, verbose=False):
        self.model_path = model_path
        self.n_gpu_layers = n_gpu_layers
        self.calls = []
        _FakeLlama.instances.append(self)

    def __call__(self, prompt, max_tokens=1024, temperature=0.0, stop=None):
        self.calls.append(
            {"prompt": prompt, "max_tokens": max_tokens, "temperature": temperature, "stop": stop}
        )
        return {"choices": [{"text": "translated text"}]}


class _FakeLlamaGpuFail:
    """首次构造抛异常（模拟 GPU 加载失败），之后正常。"""

    def __init__(self, model_path, n_ctx=2048, n_gpu_layers=0, verbose=False):
        raise RuntimeError("CUDA 不可用 (mock)")


class _FakeLlamaCpu:
    """GPU 失败回退后的 CPU 实例。"""

    def __init__(self, model_path, n_ctx=2048, n_gpu_layers=0, verbose=False):
        self.model_path = model_path
        self.n_gpu_layers = n_gpu_layers
        self.calls = []

    def __call__(self, prompt, max_tokens=1024, temperature=0.0, stop=None):
        return {"choices": [{"text": "cpu fallback ok"}]}


class TestTranslateGemmaGgufIntegration(unittest.TestCase):
    def setUp(self):
        # 注入假的 llama_cpp 模块，避免依赖真实安装
        self._saved_llama_cpp = sys.modules.get("llama_cpp")
        fake = types.ModuleType("llama_cpp")
        fake.Llama = _FakeLlama
        sys.modules["llama_cpp"] = fake

        self.server = _load_server_module()
        self.server.MODEL_REGISTRY.clear()
        self.server._MODEL_LAST_USED.clear()
        self.server.MODEL_LOAD_STATUS.clear()
        self.server.MODEL_LOAD_ERRORS.clear()

        # 本地缓存目录：含 Q4_K_M gguf 与一个 mmproj（应被排除）
        self._tmpdir = Path(tempfile.mkdtemp(prefix="gguf-test-"))
        (self._tmpdir / "translategemma-4b-it.Q4_K_M.gguf").write_bytes(b"fake-gguf")
        (self._tmpdir / "translategemma-4b-it.mmproj-Q8_0.gguf").write_bytes(b"mmproj")

        patcher = mock.patch.object(self.server, "resolve_local_model_path", return_value=str(self._tmpdir))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.server.MODEL_REGISTRY.clear()
        self.server._MODEL_LAST_USED.clear()
        self.server.MODEL_LOAD_STATUS.clear()
        self.server.MODEL_LOAD_ERRORS.clear()
        for key in list(self.server.MODEL_LOAD_EVENTS):
            self.server.MODEL_LOAD_EVENTS.pop(key, None)
        if self._saved_llama_cpp is not None:
            sys.modules["llama_cpp"] = self._saved_llama_cpp
        else:
            sys.modules.pop("llama_cpp", None)

    def test_registered_in_configs_and_capabilities(self):
        configs = self.server.MODEL_CONFIGS
        self.assertIn("translategemma-4b-it-gguf", configs)
        cfg = configs["translategemma-4b-it-gguf"]
        self.assertEqual(cfg.get("type"), "translation")
        self.assertEqual(cfg.get("format"), "gguf")
        self.assertEqual(cfg.get("model"), "mradermacher/translategemma-4b-it-GGUF")
        caps = self.server.MODEL_CAPABILITIES["translategemma-4b-it-gguf"]
        self.assertTrue(caps.get("translation"))

    def test_gguf_downloads_to_shared_cache_when_local_missing(self):
        """本地缓存未命中时自动从 HF 下载（snapshot_download，落本机共享缓存）后加载。"""
        download_dir = Path(tempfile.mkdtemp(prefix="gguf-dl-"))
        (download_dir / "translategemma-4b-it.Q4_K_M.gguf").write_bytes(b"downloaded-gguf")

        resolve_none = mock.patch.object(self.server, "resolve_local_model_path", return_value=None)
        dl_patch = mock.patch("huggingface_hub.snapshot_download", return_value=str(download_dir))
        with resolve_none, dl_patch as dl_mock:
            model = self.server.load_model("translategemma-4b-it-gguf")

        self.assertTrue(hasattr(model, "translate"))
        # llama 使用下载后定位到的 .gguf 权重路径
        self.assertTrue(
            _FakeLlama.instances[-1].model_path.endswith("translategemma-4b-it.Q4_K_M.gguf")
        )
        dl_mock.assert_called_once()

    def test_gguf_download_failure_raises_file_not_found(self):
        """下载失败时抛出 FileNotFoundError（含原因），不静默吞错。"""
        resolve_none = mock.patch.object(self.server, "resolve_local_model_path", return_value=None)
        dl_fail = mock.patch(
            "huggingface_hub.snapshot_download",
            side_effect=RuntimeError("network down (mock)"),
        )
        with resolve_none, dl_fail:
            with self.assertRaises(FileNotFoundError) as ctx:
                self.server.load_model("translategemma-4b-it-gguf")
        self.assertIn("network down", str(ctx.exception))

    def test_loads_gguf_with_gpu_offload(self):
        """加载走 llama_cpp.Llama，n_gpu_layers=-1 全量 GPU；选中的是 Q4_K_M 而非 mmproj。"""
        _FakeLlama.instances.clear()
        model = self.server.load_model("translategemma-4b-it-gguf", device="cuda")

        self.assertTrue(hasattr(model, "translate"))
        self.assertEqual(len(_FakeLlama.instances), 1)
        instance = _FakeLlama.instances[0]
        self.assertTrue(instance.model_path.endswith("translategemma-4b-it.Q4_K_M.gguf"))
        self.assertEqual(instance.n_gpu_layers, -1)

    def test_translate_builds_gemma3_prompt_with_iso_codes(self):
        """prompt 手动拼 Gemma3 chat template，FLORES 码映射为 ISO 639-1。"""
        _FakeLlama.instances.clear()
        model = self.server.load_model("translategemma-4b-it-gguf", device="cuda")
        result = model.translate("你好", "zho_Hans", "eng_Latn")

        self.assertEqual(result, "translated text")
        call = _FakeLlama.instances[0].calls[-1]
        prompt = call["prompt"]
        self.assertIn("Translate the following text from zh to en", prompt)
        self.assertIn("你好", prompt)
        self.assertTrue(prompt.startswith("<start_of_turn>user\n"))
        self.assertIn("<end_of_turn>\n<start_of_turn>model\n", prompt)
        # greedy 解码 + max_tokens 下限 1024
        self.assertEqual(call["temperature"], 0.0)
        self.assertGreaterEqual(call["max_tokens"], 1024)
        self.assertEqual(call["stop"], ["<end_of_turn>"])

    def test_translate_rejects_unknown_language_code(self):
        _FakeLlama.instances.clear()
        model = self.server.load_model("translategemma-4b-it-gguf", device="cuda")
        with self.assertRaises(ValueError):
            model.translate("hi", "xxx_XXX", "eng_Latn")

    def test_translate_chunks_long_text(self):
        """>1200 字文本按句切块，逐块调用 llama 并拼接。"""
        _FakeLlama.instances.clear()
        model = self.server.load_model("translategemma-4b-it-gguf", device="cuda")
        long_text = "长" * 750 + "。" + "长" * 750 + "。"
        result = model.translate(long_text, "zho_Hans", "eng_Latn")

        self.assertEqual(len(_FakeLlama.instances[0].calls), 2)
        self.assertEqual(result, "translated text\ntranslated text")

    def test_gpu_load_failure_falls_back_to_cpu(self):
        """GPU 加载失败时以 n_gpu_layers=0 重试（CPU）。"""
        fake = types.ModuleType("llama_cpp")

        class _Sequence:
            def __init__(self):
                self.count = 0

            def __call__(self, model_path, n_ctx=2048, n_gpu_layers=0, verbose=False):
                self.count += 1
                if self.count == 1:
                    raise RuntimeError("CUDA 不可用 (mock)")
                return _FakeLlamaCpu(model_path=model_path, n_ctx=n_ctx, n_gpu_layers=n_gpu_layers, verbose=verbose)

        seq = _Sequence()
        fake.Llama = seq
        with mock.patch.dict(sys.modules, {"llama_cpp": fake}):
            # 重新加载模块使 server 使用新 mock（原模块内已绑定旧 Llama，需重置）
            self.server.MODEL_REGISTRY.clear()
            self.server.MODEL_LOAD_STATUS.clear()
            model = self.server.load_model("translategemma-4b-it-gguf", device="cuda")

        self.assertEqual(model.llm.n_gpu_layers, 0)
        self.assertEqual(model.translate("hi", "eng_Latn", "zho_Hans"), "cpu fallback ok")

    def test_gguf_falls_back_to_cpu_when_vram_insufficient(self):
        """显存不足时先卸载 FunASR；仍不足则以 n_gpu_layers=0 加载，避免 llama.cpp abort 崩溃（网关 502）。"""
        import torch

        _FakeLlama.instances.clear()
        # 预置一个 FunASR 模型（非翻译模型）
        funasr_key = "sensevoice::device=cuda"

        class _FakeFunasr:
            def transcribe(self, *a, **k):
                return {}

        self.server.MODEL_REGISTRY[funasr_key] = _FakeFunasr()
        self.server.MODEL_LOAD_STATUS["sensevoice"] = {"state": "ready", "error": None, "updated_at": 0.0}

        with mock.patch.object(torch.cuda, "is_available", return_value=True), mock.patch.object(
            torch.cuda, "mem_get_info", side_effect=[(3 * 1024 ** 3, 10 * 1024 ** 3)] * 2
        ):
            model = self.server.load_model("translategemma-4b-it-gguf", device="cuda")

        self.assertTrue(hasattr(model, "translate"))
        self.assertEqual(_FakeLlama.instances[0].n_gpu_layers, 0)
        # FunASR 被卸载腾出显存
        self.assertNotIn(funasr_key, self.server.MODEL_REGISTRY)
        self.assertEqual(self.server.MODEL_LOAD_STATUS["sensevoice"]["state"], "unloaded")

    def test_gguf_unloads_funasr_then_uses_gpu_when_vram_freed(self):
        """显存不足时卸载 FunASR 后余量充足，GGUF 仍走 GPU（n_gpu_layers=-1）。"""
        import torch

        _FakeLlama.instances.clear()
        funasr_key = "sensevoice::device=cuda"

        class _FakeFunasr:
            def transcribe(self, *a, **k):
                return {}

        self.server.MODEL_REGISTRY[funasr_key] = _FakeFunasr()

        with mock.patch.object(torch.cuda, "is_available", return_value=True), mock.patch.object(
            torch.cuda, "mem_get_info", side_effect=[(3 * 1024 ** 3, 10 * 1024 ** 3), (8 * 1024 ** 3, 10 * 1024 ** 3)]
        ):
            model = self.server.load_model("translategemma-4b-it-gguf", device="cuda")

        self.assertTrue(hasattr(model, "translate"))
        self.assertEqual(_FakeLlama.instances[0].n_gpu_layers, -1, "卸载 FunASR 后应恢复 GPU")
        self.assertNotIn(funasr_key, self.server.MODEL_REGISTRY)

    def test_missing_gguf_file_raises(self):
        """本地缓存目录没有 .gguf 且下载也失败时给出明确错误。"""
        empty_dir = Path(tempfile.mkdtemp(prefix="gguf-empty-"))
        dl_fail = mock.patch(
            "huggingface_hub.snapshot_download",
            side_effect=RuntimeError("cannot download (mock)"),
        )
        with mock.patch.object(self.server, "resolve_local_model_path", return_value=str(empty_dir)):
            with dl_fail:
                with self.assertRaises(FileNotFoundError):
                    self.server.load_model("translategemma-4b-it-gguf", device="cuda")


if __name__ == "__main__":
    unittest.main()
