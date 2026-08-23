"""
程序说明：
模型别名与 model id 映射的单元测试（unittest）。

目标：
- 避免把别名与官方文档口径搞混，确保 server.py 的 MODEL_CONFIGS 与文档一致。
- 测试只做静态配置校验，不触发模型加载/下载。
"""

import sys
import unittest
import importlib.util
import threading
import time
import types
from pathlib import Path
from unittest import mock


_ROOT = Path(__file__).resolve().parents[1]
_OPENAI_API_DIR = _ROOT / "app" / "openai_api"
_SERVER_PATH = _OPENAI_API_DIR / "server.py"
_SCRIPTS_DIR = _ROOT / "scripts"
_BATCH_TRANSCRIBE_PATH = _SCRIPTS_DIR / "batch_transcribe.py"


def _load_server_module():
    sys.path.insert(0, str(_OPENAI_API_DIR))
    try:
        spec = importlib.util.spec_from_file_location("funasr_openai_api_server", _SERVER_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载 server 模块：{_SERVER_PATH}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if sys.path and sys.path[0] == str(_OPENAI_API_DIR):
            sys.path.pop(0)

def _load_batch_transcribe_module():
    spec = importlib.util.spec_from_file_location("funasr_batch_transcribe", _BATCH_TRANSCRIBE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 batch_transcribe 模块：{_BATCH_TRANSCRIBE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestModelConfigs(unittest.TestCase):
    def test_model_id_mappings_align_official(self):
        server = _load_server_module()
        cfgs = server.MODEL_CONFIGS

        # SenseVoice / Fun-ASR-Nano / Qwen3-ASR 官方链路原生带标点，不默认挂外置 PUNC。
        self.assertNotIn("punc_model", cfgs["sensevoice"])
        self.assertEqual(cfgs["paraformer"].get("punc_model"), "ct-punc")
        self.assertEqual(cfgs["paraformer-zh-streaming"].get("punc_model"), "ct-punc")

        self.assertEqual(cfgs["fun-asr-nano"]["model"], "FunAudioLLM/Fun-ASR-Nano-2512")
        self.assertEqual(cfgs["fun-asr-nano"].get("hub"), "ms")
        self.assertTrue(cfgs["fun-asr-nano"].get("trust_remote_code"))

        self.assertEqual(cfgs["qwen3-asr"]["model"], "Qwen/Qwen3-ASR-1.7B")
        self.assertEqual(cfgs["qwen3-asr"].get("hub"), "ms")
        self.assertTrue(cfgs["qwen3-asr"].get("trust_remote_code"))
        self.assertEqual(cfgs["qwen3-asr"].get("dtype"), "fp16")

        self.assertEqual(cfgs["qwen3-asr-0.6b"]["model"], "Qwen/Qwen3-ASR-0.6B")
        self.assertEqual(cfgs["qwen3-asr-0.6b"].get("hub"), "ms")
        self.assertTrue(cfgs["qwen3-asr-0.6b"].get("trust_remote_code"))
        self.assertEqual(cfgs["qwen3-asr-0.6b"].get("dtype"), "fp16")

        self.assertEqual(cfgs["emotion2vec-plus-large"]["model"], "iic/emotion2vec_plus_large")
        self.assertEqual(cfgs["emotion2vec-plus-large"].get("hub"), "ms")

    def test_batch_transcribe_configs_align_server(self):
        server = _load_server_module()
        batch = _load_batch_transcribe_module()

        server_cfgs = server.MODEL_CONFIGS
        batch_cfgs = batch.MODEL_CONFIGS

        for alias in ("sensevoice", "paraformer", "fun-asr-nano", "qwen3-asr"):
            self.assertIn(alias, batch_cfgs)
            self.assertIn(alias, server_cfgs)
            self.assertEqual(batch_cfgs[alias]["model"], server_cfgs[alias]["model"])
            self.assertEqual(batch_cfgs[alias].get("hub"), server_cfgs[alias].get("hub"))
            self.assertEqual(batch_cfgs[alias].get("trust_remote_code"), server_cfgs[alias].get("trust_remote_code"))
            self.assertEqual(batch_cfgs[alias].get("punc_model"), server_cfgs[alias].get("punc_model"))

    def test_model_capabilities_align_routes(self):
        server = _load_server_module()

        caps = server.MODEL_CAPABILITIES
        self.assertTrue(caps["sensevoice"]["offline_asr"])
        self.assertTrue(caps["sensevoice"]["emotion"])
        self.assertTrue(caps["sensevoice"]["diarization"])
        self.assertTrue(caps["sensevoice"]["vad"])
        self.assertTrue(caps["paraformer"]["punc"])
        self.assertTrue(caps["paraformer-zh-streaming"]["streaming_asr"])
        self.assertFalse(caps["paraformer-zh-streaming"]["offline_asr"])
        self.assertIn("paraformer-zh-streaming", server.STREAMING_MODELS)
        self.assertTrue(caps["emotion2vec-plus-large"]["emotion"])
        self.assertFalse(caps["emotion2vec-plus-large"]["offline_asr"])
        self.assertIn("emotion2vec-plus-large", server.EMOTION_MODELS)
        self.assertIn("sensevoice", server.EMOTION_MODELS)
        self.assertTrue(caps["paraformer"]["diarization"])
        self.assertIn("paraformer", server.DIARIZATION_MODELS)
        self.assertIn("sensevoice", server.DIARIZATION_MODELS)
        self.assertIn("fun-asr-nano", server.DIARIZATION_MODELS)

    def test_model_ready_detects_loaded_variants(self):
        server = _load_server_module()
        server.MODEL_REGISTRY.clear()
        server.MODEL_REGISTRY["paraformer::device=cpu|hub=ms|disable_update=True|ncpu=|log_level=|disable_pbar=|punc_model=ct-punc"] = object()
        self.assertTrue(server._is_model_ready("paraformer"))
        self.assertFalse(server._is_model_ready("sensevoice"))

    def test_runtime_config_accepts_explicit_forced_aligner(self):
        server = _load_server_module()
        cfg = server.build_model_runtime_config(
            model_name="qwen3-asr",
            device="cpu",
            hub=None,
            disable_update=True,
            ncpu=None,
            log_level=None,
            disable_pbar=None,
            punc_mode="auto",
            forced_aligner="custom/aligner",
        )
        self.assertEqual(cfg["forced_aligner"], "custom/aligner")
        self.assertIn("forced_aligner=custom/aligner", server.build_model_registry_key("qwen3-asr", cfg))

    def test_resolve_local_model_path_uses_alias_without_network(self):
        from tempfile import TemporaryDirectory

        server = _load_server_module()
        with TemporaryDirectory() as temp_dir:
            model_dir = (
                Path(temp_dir)
                / "iic"
                / "speech_campplus_sv_zh-cn_16k-common"
            )
            model_dir.mkdir(parents=True)
            (model_dir / "configuration.json").write_text("{}", encoding="utf-8")
            (model_dir / "campplus_cn_common.bin").write_bytes(b"weight")

            resolved = server.resolve_local_model_path("cam++", [temp_dir])

            self.assertEqual(resolved, model_dir.resolve())

    def test_concurrent_first_load_uses_single_flight(self):
        server = _load_server_module()
        server.MODEL_REGISTRY.clear()
        server.MODEL_LOAD_STATUS.clear()
        server.MODEL_LOAD_EVENTS.clear()
        server.MODEL_LOAD_ERRORS.clear()

        calls = []
        loaded_model = object()

        def fake_auto_model(**_kwargs):
            calls.append(time.monotonic())
            time.sleep(0.15)
            return loaded_model

        fake_funasr = types.SimpleNamespace(AutoModel=fake_auto_model)
        results = []
        errors = []

        def worker():
            try:
                results.append(server.load_model("paraformer", device="cpu"))
            except Exception as exc:  # pragma: no cover - 失败时由断言显示
                errors.append(exc)

        with mock.patch.dict(sys.modules, {"funasr": fake_funasr}):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=3)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertIs(results[0], loaded_model)
        self.assertIs(results[1], loaded_model)
        self.assertEqual(len(calls), 1)

    def test_missing_local_model_does_not_leave_single_flight_waiter(self):
        server = _load_server_module()
        server.MODEL_LOAD_EVENTS.clear()

        with mock.patch.object(
            server,
            "_resolve_runtime_models_to_local",
            side_effect=server.ModelNotDownloadedError("missing"),
        ):
            with self.assertRaises(server.ModelNotDownloadedError):
                server.load_model("sensevoice")

        self.assertEqual(server.MODEL_LOAD_EVENTS, {})


if __name__ == "__main__":
    unittest.main()
