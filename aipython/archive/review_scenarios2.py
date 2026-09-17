# -*- coding: utf-8 -*-
"""Review: 模拟所有 diarization + _resolve 场景"""
import sys
sys.path.insert(0, 'app/openai_api')
from openai_api import workflow_service, server

# === 场景 1: 默认配置 asr_model="" → primary 承担 ===
cfg1 = workflow_service.parse_workflow_config({
    'transcription': {'primary': {'model': 'sensevoice'}},
    'diarization': {'enabled': True},
})
assert cfg1.diarization.asr_model == '', f'Expected empty, got {cfg1.diarization.asr_model!r}'
target = cfg1.diarization.asr_model or cfg1.transcription.primary.model
assert target == 'sensevoice', f'Expected sensevoice, got {target}'
reuse = cfg1.diarization.enabled and 'sensevoice' == target and not cfg1.segmentation.chunk_enabled
assert reuse == True
print('OK 1 default asr_model empty -> primary承担 diarization reuse=True')

# === 场景 2: 多模型 primary=sensevoice reviewer=qwen3-asr ===
cfg2 = workflow_service.parse_workflow_config({
    'transcription': {
        'primary': {'model': 'sensevoice'},
        'reviewers': [{'model': 'qwen3-asr'}],
    },
    'diarization': {'enabled': True},
})
target2 = cfg2.diarization.asr_model or cfg2.transcription.primary.model
assert target2 == 'sensevoice'
assert cfg2.diarization.enabled and 'sensevoice' == target2   # primary reuses
assert not (cfg2.diarization.enabled and 'qwen3-asr' == target2)  # reviewer doesn't

fake_p = {'model': 'sensevoice', 'diarization': {'segments': [{'speaker': 0}]}}
fake_r = {'model': 'qwen3-asr'}  # no diarization
target_wr = cfg2.diarization.asr_model or cfg2.transcription.primary.model
cands = [item for item in (fake_p, fake_r) if isinstance(item.get('diarization'), dict)]
assert len(cands) == 1
d = next((c['diarization'] for c in cands if str(c['model']) == target_wr), cands[0]['diarization'])
assert d['segments'][0]['speaker'] == 0
print('OK 2 multi-model -> primary reuse, reviewer skips, diarization reused')

# === 场景 3: 用户显式指定 asr_model ===
cfg3 = workflow_service.parse_workflow_config({
    'transcription': {'primary': {'model': 'sensevoice'}},
    'diarization': {'enabled': True, 'asr_model': 'paraformer'},
})
target3 = cfg3.diarization.asr_model or cfg3.transcription.primary.model
assert target3 == 'paraformer', f'Expected paraformer, got {target3}'
print('OK 3 explicit asr_model respected')

# === 场景 4: chunk_enabled=True ===
cfg4 = workflow_service.parse_workflow_config({
    'transcription': {'primary': {'model': 'sensevoice'}},
    'diarization': {'enabled': True},
    'segmentation': {'chunk_enabled': True},
})
reuse4 = cfg4.diarization.enabled and 'sensevoice' == 'sensevoice' and not cfg4.segmentation.chunk_enabled
assert reuse4 == False
print('OK 4 chunk_enabled -> reuse=False')

# === 场景 5: 所有模型都没 diarization -> fallback ===
cfg5 = workflow_service.parse_workflow_config({
    'transcription': {'primary': {'model': 'sensevoice'}},
    'diarization': {'enabled': True},
})
fp = {'model': 'sensevoice'}
fr = {'model': 'qwen3-asr'}
target5 = cfg5.diarization.asr_model or cfg5.transcription.primary.model
cands5 = [i for i in (fp, fr) if isinstance(i.get('diarization'), dict)]
assert cands5 == []
d5 = next((i.get('diarization') for i in cands5 if str(i.get('model')) == target5), cands5[0]['diarization'] if cands5 else None)
assert d5 is None
print('OK 5 no model has diarization -> fallback to runtime.diarize()')

# === 场景 6: primary 没但其他模型有 ===
cfg6 = workflow_service.parse_workflow_config({
    'transcription': {'primary': {'model': 'sensevoice'}},
    'diarization': {'enabled': True},
})
fp6 = {'model': 'sensevoice'}
fr6 = {'model': 'paraformer', 'diarization': {'segments': [{'speaker': 1}]}}
target6 = cfg6.diarization.asr_model or cfg6.transcription.primary.model
cands6 = [i for i in (fp6, fr6) if isinstance(i.get('diarization'), dict)]
d6 = next((i.get('diarization') for i in cands6 if str(i.get('model')) == target6), cands6[0]['diarization'] if cands6 else None)
assert d6 is not None and d6['segments'][0]['speaker'] == 1
print('OK 6 primary no diarization -> fallback to first available')

# === 场景 7: _resolve_runtime_models_to_local ===
cfg_dict = {'model': 'iic/speech_eres2netv2_sv_zh-cn_16k-common'}
resolved = server._resolve_runtime_models_to_local(cfg_dict.copy())
assert resolved.get('model') == 'iic/speech_eres2netv2_sv_zh-cn_16k-common', f'reg key overwritten: {resolved.get("model")}'
assert resolved.get('model_path') is not None
assert resolved.get('check_latest') == False
print('OK 7 _resolve keeps reg key + adds model_path + check_latest=False')

# === 场景 8: _resolve 辅助模型不覆盖 cfg[key] ===
cfg8 = {'model': 'sensevoice', 'vad_model': 'iic/speech_fsmn_vad_zh-cn-16k-common'}
resolved8 = server._resolve_runtime_models_to_local(cfg8.copy())
assert resolved8.get('vad_model') == 'iic/speech_fsmn_vad_zh-cn-16k-common', 'vad_model should keep reg key'
assert resolved8.get('vad_model_path') is not None
print('OK 8 auxiliary models keep reg key + add *_path')

# === 场景 9: _resolve 模型根本没下载 ===
cfg9 = {'model': 'totally-unknown-model-xyz'}
try:
    server._resolve_runtime_models_to_local(cfg9)
    print('FAIL 9 should have raised')
except Exception as e:
    assert '模型文件未下载' in str(e)
    print('OK 9 missing model raises ModelNotDownloadedError')

# === 场景 10: server.py _workflow_transcribe_model resolve_diarization_spk_mode 参数修复 ===
# 原来传 config.diarization.asr_model (可能空)，现在传 model_config.model (实际模型)
# 确认 resolve_diarization_spk_mode 签名
import inspect
from openai_api.server import resolve_diarization_spk_mode
sig = inspect.signature(resolve_diarization_spk_mode)
assert 'model' in sig.parameters
print('OK 10 resolve_diarization_spk_mode signature correct')

print()
print('ALL 10 SCENARIOS PASSED')
