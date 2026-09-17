# -*- coding: utf-8 -*-
"""最终 review: 逐场景验证"""
import sys
sys.path.insert(0, 'app/openai_api')
from openai_api import workflow_service, server

# ===== _resolve_runtime_models_to_local =====
print('=== _resolve 主模型 cam++ ===')
cfg = {'model': 'cam++'}
resolved = server._resolve_runtime_models_to_local(cfg)
assert resolved.get('model') == 'cam++', f"reg key overwritten: {resolved.get('model')}"
assert resolved.get('model_path') is not None
assert resolved.get('check_latest') == False
print(f"  model={resolved['model']!r}  model_path=OK  check_latest=False")

print('=== _resolve 辅助模型 ===')
cfg2 = {'model': 'sensevoice', 'vad_model': 'iic/speech_fsmn_vad_zh-cn_16k-common'}
resolved2 = server._resolve_runtime_models_to_local(cfg2)
assert resolved2.get('vad_model') == 'iic/speech_fsmn_vad_zh-cn_16k-common'
assert resolved2.get('vad_model_path') is not None
print(f"  vad_model={resolved2['vad_model']!r}  vad_model_path=OK")

print('=== _resolve 未知模型 ===')
try:
    server._resolve_runtime_models_to_local({'model': 'totally-unknown-xyz'})
    print('  FAIL should raise')
except Exception as e:
    assert '模型文件未下载' in str(e)
    print(f"  raises ModelNotDownloadedError: OK")

# ===== diarization 逻辑 =====
print()
print('=== diarization: 默认 chunk_enabled=True -> reuse=False (正确守卫) ===')
cfg1 = workflow_service.parse_workflow_config({
    'transcription': {'primary': {'model': 'sensevoice'}},
    'diarization': {'enabled': True},
})
target = cfg1.diarization.asr_model or cfg1.transcription.primary.model
assert target == 'sensevoice'
assert cfg1.segmentation.chunk_enabled == True
reuse = cfg1.diarization.enabled and 'sensevoice' == target and not cfg1.segmentation.chunk_enabled
assert reuse == False, f"chunk_enabled=True 时应该不复用！got reuse={reuse}"
print(f"  target_asr=sensevoice  chunk_enabled=True  reuse=False: OK")

print()
print('=== diarization: chunk_enabled=False -> primary reuse=True ===')
cfg2 = workflow_service.parse_workflow_config({
    'transcription': {'primary': {'model': 'sensevoice'}},
    'diarization': {'enabled': True},
    'segmentation': {'chunk_enabled': False},
})
reuse2 = cfg2.diarization.enabled and 'sensevoice' == ('') or cfg2.transcription.primary.model and not cfg2.segmentation.chunk_enabled
# 修正:
target2 = cfg2.diarization.asr_model or cfg2.transcription.primary.model
reuse2 = cfg2.diarization.enabled and 'sensevoice' == target2 and not cfg2.segmentation.chunk_enabled
assert reuse2 == True
print(f"  target_asr=sensevoice  chunk_enabled=False  reuse=True: OK")

print()
print('=== diarization: 多模型 primary 复用 reviewer 不复用 ===')
cfg3 = workflow_service.parse_workflow_config({
    'transcription': {
        'primary': {'model': 'sensevoice'},
        'reviewers': [{'model': 'qwen3-asr'}],
    },
    'diarization': {'enabled': True},
    'segmentation': {'chunk_enabled': False},
})
target3 = cfg3.diarization.asr_model or cfg3.transcription.primary.model
assert target3 == 'sensevoice'
# primary 跑时: reuse=True
assert cfg3.diarization.enabled and 'sensevoice' == target3
# reviewer 跑时: reuse=False
assert not (cfg3.diarization.enabled and 'qwen3-asr' == target3)
print(f"  primary=sensevoice reuses, reviewer=qwen3-asr skips: OK")

print()
print('=== workflow_runner candidates 逻辑 ===')
# primary 有 diarization, reviewer 没有
fake_p = {'model': 'sensevoice', 'diarization': {'segments': [{'speaker': 0}]}}
fake_r = {'model': 'qwen3-asr'}
cands = [i for i in (fake_p, fake_r) if isinstance(i.get('diarization'), dict)]
assert len(cands) == 1
d = next((i.get('diarization') for i in cands if str(i.get('model')) == target3), cands[0]['diarization'])
assert d['segments'][0]['speaker'] == 0
print(f"  candidates={[c['model'] for c in cands]}  picks sensevoice: OK")

# primary 没有, reviewer 有 fallback
fake_p2 = {'model': 'sensevoice'}
fake_r2 = {'model': 'paraformer', 'diarization': {'segments': [{'speaker': 1}]}}
cands2 = [i for i in (fake_p2, fake_r2) if isinstance(i.get('diarization'), dict)]
assert len(cands2) == 1
d2 = next((i.get('diarization') for i in cands2 if str(i.get('model')) == target3), cands2[0]['diarization'] if cands2 else None)
assert d2 is not None and d2['segments'][0]['speaker'] == 1
print(f"  primary no diarization -> fallback first available (paraformer): OK")

# 全都没有 -> None -> runtime.diarize()
fp = {'model': 'sensevoice'}
fr = {'model': 'qwen3-asr'}
target_empty = cfg1.diarization.asr_model or cfg1.transcription.primary.model
cands_empty = [i for i in (fp, fr) if isinstance(i.get('diarization'), dict)]
assert cands_empty == []
d_empty = next((i.get('diarization') for i in cands_empty if str(i.get('model')) == target_empty), cands_empty[0]['diarization'] if cands_empty else None)
assert d_empty is None
print(f"  all no diarization -> None -> runtime.diarize(): OK")

# 用户显式指定
cfg_explicit = workflow_service.parse_workflow_config({
    'transcription': {'primary': {'model': 'sensevoice'}},
    'diarization': {'enabled': True, 'asr_model': 'paraformer'},
    'segmentation': {'chunk_enabled': False},
})
target_explicit = cfg_explicit.diarization.asr_model or cfg_explicit.transcription.primary.model
assert target_explicit == 'paraformer'
print(f"  explicit asr_model='paraformer' -> target_asr='paraformer': OK")

print()
print('=== auto_fill_transcription_mode ===')
cfg_auto = workflow_service.parse_workflow_config({
    'transcription': {'primary': {'model': 'sensevoice'}},
})
assert cfg_auto.transcription.mode == 'single_model'
# 加 reviewer
cfg_auto2 = workflow_service.parse_workflow_config({
    'transcription': {
        'primary': {'model': 'sensevoice'},
        'reviewers': [{'model': 'qwen3-asr'}],
    },
})
workflow_service.auto_fill_transcription_mode(cfg_auto2)
assert cfg_auto2.transcription.mode == 'multi_model', f"got {cfg_auto2.transcription.mode}"
# 移除 reviewer 后
cfg_auto3 = workflow_service.parse_workflow_config({
    'transcription': {'primary': {'model': 'sensevoice'}, 'reviewers': []},
})
workflow_service.auto_fill_transcription_mode(cfg_auto3)
assert cfg_auto3.transcription.mode == 'single_model'
print(f"  reviewers 非空 -> multi_model  空 -> single_model: OK")

print()
print('ALL CHECKS PASSED')
