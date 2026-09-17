# -*- coding: utf-8 -*-
"""完美解法验证：cfg['model']=注册key + model_path=本地路径"""
import sys, os
sys.path.insert(0, 'app/openai_api')
sys.path.insert(0, 'app')

from openai_api import server
from openai_api.server import resolve_local_model_path, _model_cache_roots

cfg = server.build_model_runtime_config(
    model_name='qwen3-asr', device=None, hub=None, disable_update=None,
    ncpu=None, log_level=None, disable_pbar=None, punc_mode='auto',
)
local_path = resolve_local_model_path(cfg['model'], _model_cache_roots())
print(f"注册key: {cfg['model']}")
print(f"本地路径: {local_path}")

# ========= 方案 C：cfg['model']=注册key + cfg['model_path']=本地路径 =========
print("\n=== 方案 C: model=注册key + model_path=本地路径 ===")
cfg_c = cfg.copy()
cfg_c['model_path'] = str(local_path)  # ← 关键！download_from_ms 看到这个就跳过 snapshot_download
cfg_c['check_latest'] = False
print(f"  cfg_c['model'] = {cfg_c['model']}")
print(f"  cfg_c['model_path'] = {cfg_c['model_path']}")

import traceback
from funasr.auto.auto_model import AutoModel
try:
    m = AutoModel(**cfg_c)
    print(f"  ✅ 成功！type={type(m).__name__}")
except Exception as e:
    print(f"  ❌ 失败: {e}")
    traceback.print_exc()

# ========= 顺便测 sensevoice =========
print("\n=== 方案 C 也适用于 sensevoice ===")
cfg_sv = server.build_model_runtime_config(
    model_name='sensevoice', device=None, hub=None, disable_update=None,
    ncpu=None, log_level=None, disable_pbar=None, punc_mode='auto',
)
lp_sv = resolve_local_model_path(cfg_sv['model'], _model_cache_roots())
cfg_sv['model_path'] = str(lp_sv)
cfg_sv['check_latest'] = False
print(f"  model={cfg_sv['model']}, model_path={cfg_sv['model_path']}")
try:
    m2 = AutoModel(**cfg_sv)
    print(f"  ✅ 成功！type={type(m2).__name__}")
except Exception as e:
    print(f"  ❌ 失败: {e}")
