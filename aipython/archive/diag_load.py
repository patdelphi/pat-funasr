# -*- coding: utf-8 -*-
"""直接 load qwen3-asr 看 FunASR 报什么错"""
import sys, os, traceback
sys.path.insert(0, 'app/openai_api')
sys.path.insert(0, 'app')
from openai_api import server

for m in ['sensevoice', 'fun-asr-nano', 'qwen3-asr']:
    print(f"\n{'='*50}")
    print(f"Trying load_model('{m}')")
    print('='*50)
    try:
        model = server.load_model(m, punc_mode='auto')
        print(f"✅ SUCCESS: {type(model).__name__}")
    except Exception as e:
        print(f"❌ FAILED: {e}")
        traceback.print_exc()
