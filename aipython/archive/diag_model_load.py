# -*- coding: utf-8 -*-
"""诊断 qwen3-asr reviewer 模型加载失败的根因"""
import sys, os
sys.path.insert(0, 'app/openai_api')
sys.path.insert(0, 'app')
from openai_api import server

def check(model_name):
    print(f"\n=== {model_name} ===")
    cfg = server.build_model_runtime_config(
        model_name=model_name, device=None, hub=None, disable_update=None,
        ncpu=None, log_level=None, disable_pbar=None, punc_mode='auto',
    )
    print(f"  build_model_runtime_config cfg['model'] = {cfg['model']}")
    resolved = server._resolve_runtime_models_to_local(cfg.copy())
    print(f"  after _resolve cfg['model'] = {resolved['model']}")
    print(f"  path exists: {os.path.exists(resolved['model'])}")

# 测两个模型
for m in ['sensevoice', 'fun-asr-nano', 'qwen3-asr', 'qwen3-asr-0.6b', 'paraformer']:
    try:
        check(m)
    except Exception as e:
        print(f"  ❌ {e}")
