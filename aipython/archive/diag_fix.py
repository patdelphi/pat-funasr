# -*- coding: utf-8 -*-
"""验证：删 import whisper 后，Qwen3ASR 是否出现在注册 key + load_model 能否成功"""
import sys, traceback
sys.path.insert(0, 'app/openai_api')
sys.path.insert(0, 'app')

print("=== 1. FunASR 导入后 model_registry 里有什么 ===")
import funasr
from funasr.auto.auto_model import AutoModel
# 触发 auto registration
try:
    AutoModel.__init__  # access to trigger imports
except:
    pass

# 检查 qwen_audio.model 能否被导入
print("\n=== 2. 显式导入 funasr.models.qwen_audio.model ===")
try:
    import funasr.models.qwen_audio.model as qm
    print(f"✅ 导入成功: {qm}")
except Exception as e:
    print(f"❌ 仍然失败: {e}")

print("\n=== 3. 试 load_model('qwen3-asr') ===")
from openai_api import server
try:
    model = server.load_model('qwen3-asr', punc_mode='auto')
    print(f"✅ SUCCESS: {type(model).__name__}")
except Exception as e:
    print(f"❌ FAILED: {e}")
    traceback.print_exc()
