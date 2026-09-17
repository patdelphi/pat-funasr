# -*- coding: utf-8 -*-
"""验证：cfg['model']=本地路径 vs 注册key 哪种方式能让 FunASR 正确加载 + 不联网"""
import sys, os
sys.path.insert(0, 'app/openai_api')
sys.path.insert(0, 'app')

from openai_api import server

cfg = server.build_model_runtime_config(
    model_name='qwen3-asr', device=None, hub=None, disable_update=None,
    ncpu=None, log_level=None, disable_pbar=None, punc_mode='auto',
)
print(f"原始 cfg['model'] = {cfg['model']}")

from openai_api.server import resolve_local_model_path, _model_cache_roots
local_path = resolve_local_model_path(cfg['model'], _model_cache_roots())
print(f"本地路径 = {local_path}")

import traceback
from funasr.auto.auto_model import AutoModel

# ========= 方案 A：cfg['model'] = 本地路径（旧代码行为，但 cfg 没被覆盖导致注册失败）=========
print("\n=== 方案 A: cfg['model'] = 本地路径 ===")
cfg_a = cfg.copy()
cfg_a['model'] = str(local_path)
cfg_a['check_latest'] = False
print(f"  cfg_a['model'] = {cfg_a['model']}")
try:
    # 先让 FunASR 走 download_model 内部看看 kwargs['model'] 变成啥
    from funasr.download.download_model_from_hub import download_from_ms
    kwargs_a = cfg_a.copy()
    kwargs_a_after = download_from_ms(**kwargs_a)
    print(f"  download_from_ms 后 kwargs['model'] = {kwargs_a_after.get('model')}")
    print(f"  download_from_ms 后 kwargs['model_path'] = {kwargs_a_after.get('model_path')}")

    m = AutoModel(**cfg_a)
    print(f"  ✅ 成功！type={type(m).__name__}")
except Exception as e:
    print(f"  ❌ 失败: {e}")
    # traceback.print_exc()

# ========= 方案 B：cfg['model'] = 注册 key（新代码行为，会联网）=========
print("\n=== 方案 B: cfg['model'] = 注册 key ===")
cfg_b = cfg.copy()
cfg_b['check_latest'] = False
print(f"  cfg_b['model'] = {cfg_b['model']}")
try:
    kwargs_b = cfg_b.copy()
    kwargs_b_after = download_from_ms(**kwargs_b)
    print(f"  download_from_ms 后 kwargs['model'] = {kwargs_b_after.get('model')}")
    print(f"  download_from_ms 后 kwargs['model_path'] = {kwargs_b_after.get('model_path')}")
    print(f"  ⚠️ 上面如果显示 Downloading 就是联网了")

    m = AutoModel(**cfg_b)
    print(f"  ✅ 成功！type={type(m).__name__}")
except Exception as e:
    print(f"  ❌ 失败: {e}")
