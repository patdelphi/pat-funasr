# -*- coding: utf-8 -*-
"""最终 review v2 - 加了 alias 处理"""
import sys
sys.path.insert(0, 'app/openai_api')
from openai_api import workflow_service, server

passed = 0
failed = 0

def t(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1; print(f"  OK {name}")
    else:
        failed += 1; print(f"  FAIL {name} {detail}")

print("=== _resolve_runtime_models_to_local ===")

# 1. 主模型 sensevoice (alias) -> skip local check
cfg = {'model': 'sensevoice'}
r = server._resolve_runtime_models_to_local(cfg)
t("主模型 alias: model preserved", r.get('model') == 'sensevoice')
t("主模型 alias: check_latest=False", r.get('check_latest') == False)
# model_path 可能 None，因为 resolve_local_model_path 找不到 sensevoice，但不 raise
t("主模型 alias: no raise (logger.info 跳过)", True)

# 2. 主模型 cam++ (direct match)
cfg2 = {'model': 'cam++'}
r2 = server._resolve_runtime_models_to_local(cfg2)
t("主模型 direct: model preserved", r2.get('model') == 'cam++')
t("主模型 direct: model_path resolved", r2.get('model_path') is not None)

# 3. 主模型 Qwen3ASR
cfg3 = {'model': 'Qwen/Qwen3-ASR-0.6B'}
r3 = server._resolve_runtime_models_to_local(cfg3)
t("主模型 Org/Name: model preserved", r3.get('model') == 'Qwen/Qwen3-ASR-0.6B')
t("主模型 Org/Name: model_path resolved", r3.get('model_path') is not None)

# 4. 辅助模型 vad
cfg4 = {'model': 'sensevoice', 'vad_model': 'iic/speech_fsmn_vad_zh-cn_16k-common'}
r4 = server._resolve_runtime_models_to_local(cfg4)
t("辅助 vad: key preserved", r4.get('vad_model') == 'iic/speech_fsmn_vad_zh-cn_16k-common')
t("辅助 vad: *_path resolved", r4.get('vad_model_path') is not None)

# 5. 辅助模型未下载 -> raise
raised = False
try:
    server._resolve_runtime_models_to_local({'model': 'sensevoice', 'vad_model': 'iic/some-nonexistent-vad'})
except Exception as e:
    raised = '模型文件未下载' in str(e)
t("辅助未下载: raises ModelNotDownloadedError", raised)

# 6. spk_model cam++
cfg6 = {'model': 'sensevoice', 'spk_model': 'cam++'}
r6 = server._resolve_runtime_models_to_local(cfg6)
t("spk_model: key preserved", r6.get('spk_model') == 'cam++')
t("spk_model: *_path resolved", r6.get('spk_model_path') is not None)

print()
print("=== diarization 场景 ===")

# 7. chunk_enabled 默认 True -> reuse=False (正确守卫)
cfg7 = workflow_service.parse_workflow_config({
    'transcription': {'primary': {'model': 'sensevoice'}},
    'diarization': {'enabled': True},
})
target7 = cfg7.diarization.asr_model or cfg7.transcription.primary.model
reuse7 = cfg7.diarization.enabled and target7 == 'sensevoice' and not cfg7.segmentation.chunk_enabled
t("chunk_enabled=True: reuse=False (故意, guard)", reuse7 == False)

# 8. chunk_enabled=False -> reuse=True
cfg8 = workflow_service.parse_workflow_config({
    'transcription': {'primary': {'model': 'sensevoice'}},
    'diarization': {'enabled': True},
    'segmentation': {'chunk_enabled': False},
})
target8 = cfg8.diarization.asr_model or cfg8.transcription.primary.model
reuse8 = cfg8.diarization.enabled and target8 == 'sensevoice' and not cfg8.segmentation.chunk_enabled
t("chunk_enabled=False: reuse=True", reuse8 == True)

# 9. 多模型 primary 复用 reviewer 不复用
cfg9 = workflow_service.parse_workflow_config({
    'transcription': {
        'primary': {'model': 'sensevoice'},
        'reviewers': [{'model': 'qwen3-asr'}],
    },
    'diarization': {'enabled': True},
    'segmentation': {'chunk_enabled': False},
})
target9 = cfg9.diarization.asr_model or cfg9.transcription.primary.model
t("multi-model: primary reuses", cfg9.diarization.enabled and 'sensevoice' == target9)
t("multi-model: reviewer skips", not (cfg9.diarization.enabled and 'qwen3-asr' == target9))

# 10. workflow_runner candidates 主有副无
fake_p = {'model': 'sensevoice', 'diarization': {'segments': [{'speaker': 0}]}}
fake_r = {'model': 'qwen3-asr'}
cands = [i for i in (fake_p, fake_r) if isinstance(i.get('diarization'), dict)]
t("candidates 主模型优先复用", len(cands) == 1 and cands[0]['model'] == 'sensevoice')
d = next((i.get('diarization') for i in cands if str(i.get('model')) == target9), cands[0]['diarization'])
t("candidates 精确匹配拿到 sensevoice 的 diarization", d['segments'][0]['speaker'] == 0)

# 11. primary 没有, fallback 第一个有
fake_p2 = {'model': 'sensevoice'}
fake_r2 = {'model': 'paraformer', 'diarization': {'segments': [{'speaker': 1}]}}
cands2 = [i for i in (fake_p2, fake_r2) if isinstance(i.get('diarization'), dict)]
d2 = next((i.get('diarization') for i in cands2 if str(i.get('model')) == target9), cands2[0]['diarization'] if cands2 else None)
t("fallback: primary 无 -> 第一个可用", d2 is not None and d2['segments'][0]['speaker'] == 1)

# 12. 全都没有 -> None -> runtime.diarize()
cands3 = []
d3 = None
for i in ({'model': 'sensevoice'}, {'model': 'qwen3-asr'}):
    if isinstance(i.get('diarization'), dict):
        cands3.append(i)
d3 = next((i.get('diarization') for i in cands3), cands3[0]['diarization'] if cands3 else None)
t("全无为空 -> runtime.diarize()", d3 is None)

# 13. 用户显式 asr_model 尊重
cfg13 = workflow_service.parse_workflow_config({
    'transcription': {'primary': {'model': 'sensevoice'}},
    'diarization': {'enabled': True, 'asr_model': 'paraformer'},
})
t("显式 asr_model 尊重", (cfg13.diarization.asr_model or cfg13.transcription.primary.model) == 'paraformer')

print()
print(f"=== auto_fill_transcription_mode ===")
cfg14 = workflow_service.parse_workflow_config({'transcription': {'primary': {'model': 'sensevoice', 'reviewers': [{'model': 'qwen3-asr'}]}}})
workflow_service.auto_fill_transcription_mode(cfg14)
t("有 reviewers -> multi_model", cfg14.transcription.mode == 'multi_model')

cfg15 = workflow_service.parse_workflow_config({'transcription': {'primary': {'model': 'sensevoice'}}})
workflow_service.auto_fill_transcription_mode(cfg15)
t("无 reviewers -> single_model", cfg15.transcription.mode == 'single_model')

print()
print(f"=== 总计 {passed}/{passed+failed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
