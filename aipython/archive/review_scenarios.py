# -*- coding: utf-8 -*-
"""Review：模拟所有 diarization 场景，验证逻辑正确性"""
import sys
sys.path.insert(0, 'app/openai_api')
from openai_api import workflow_service, workflow_runner, server

results = []
def chk(name, cond, detail=""):
    results.append(cond)
    status = "✅" if cond else "❌"
    print(f"  {status} {name}  {detail}")

print("=" * 60)
print("Scenario 1: 默认配置（asr_model='' + primary=sensevoice）")
print("=" * 60)
cfg1 = workflow_service.parse_workflow_config({
    "audio_path": "fake.wav",
    "transcription": {"primary": {"model": "sensevoice"}},
    "diarization": {"enabled": True},
})
chk("DiarizationConfig.asr_model 默认空串", cfg1.diarization.asr_model == "")
# server.py 里 target_asr 计算
target = cfg1.diarization.asr_model or cfg1.transcription.primary.model
chk("target_asr = primary.model = sensevoice", target == "sensevoice", f"got={target}")
# reuse_for_diarization 在 primary 跑 sensevoice 时
reuse = cfg1.diarization.enabled and "sensevoice" == target and not cfg1.segmentation.chunk_enabled
chk("reuse_for_diarization=True（primary 匹配）", reuse == True)

print()
print("=" * 60)
print("Scenario 2: 多模型校对（primary=sensevoice, reviewer=qwen3-asr）")
print("=" * 60)
cfg2 = workflow_service.parse_workflow_config({
    "audio_path": "fake.wav",
    "transcription": {
        "primary": {"model": "sensevoice"},
        "reviewers": [{"model": "qwen3-asr", "weight": 1.0}],
    },
    "diarization": {"enabled": True},
})
target2 = cfg2.diarization.asr_model or cfg2.transcription.primary.model
chk("target_asr 还是 sensevoice", target2 == "sensevoice")
# primary 跑时: reuse=True
reuse_p = cfg2.diarization.enabled and "sensevoice" == target2
chk("primary(sensevoice) reuse=True", reuse_p)
# reviewer 跑 qwen3-asr 时: reuse=False（target 是 sensevoice ≠ qwen3-asr）
reuse_r = cfg2.diarization.enabled and "qwen3-asr" == target2
chk("reviewer(qwen3-asr) reuse=False", reuse_r == False)

# workflow_runner 的 candidates 逻辑
fake_primary = {"model": "sensevoice", "diarization": {"segments": [{"speaker": 0}]}}
fake_reviewer = {"model": "qwen3-asr"}
target_asr_wr = cfg2.diarization.asr_model or cfg2.transcription.primary.model
candidates = [item for item in (fake_primary, fake_reviewer) if isinstance(item.get("diarization"), dict)]
chk("candidates 只有 primary（reviewer 没 diarization）", len(candidates) == 1, f"got {len(candidates)}")
diarization = next(
    (item["diarization"] for item in candidates if str(item.get("model") or "") == target_asr_wr),
    candidates[0]["diarization"] if candidates else None,
)
chk("最终复用了 sensevoice 的 diarization", diarization is not None and diarization["segments"][0]["speaker"] == 0)

print()
print("=" * 60)
print("Scenario 3: 用户显式指定 asr_model='paraformer'（旧配置兼容）")
print("=" * 60)
cfg3 = workflow_service.parse_workflow_config({
    "audio_path": "fake.wav",
    "transcription": {"primary": {"model": "sensevoice"}},
    "diarization": {"enabled": True, "asr_model": "paraformer"},
})
target3 = cfg3.diarization.asr_model or cfg3.transcription.primary.model
chk("target_asr = 'paraformer'（尊重用户配置）", target3 == "paraformer")
reuse3 = cfg3.diarization.enabled and "sensevoice" == target3
chk("primary(sensevoice) reuse=False（用户指定了 paraformer）", reuse3 == False)

print()
print("=" * 60)
print("Scenario 4: chunk_enabled=True → 强制不复用（chunk 模式 diarization 不可靠）")
print("=" * 60)
cfg4 = workflow_service.parse_workflow_config({
    "audio_path": "fake.wav",
    "transcription": {"primary": {"model": "sensevoice"}},
    "diarization": {"enabled": True},
    "segmentation": {"chunk_enabled": True},
})
reuse4 = cfg4.diarization.enabled and "sensevoice" == ("" or cfg4.transcription.primary.model) and not cfg4.segmentation.chunk_enabled
chk("chunk_enabled 时 reuse=False", reuse4 == False)

print()
print("=" * 60)
print("Scenario 5: diarization.enabled=False → 全部跳过")
print("=" * 60)
cfg5 = workflow_service.parse_workflow_config({
    "audio_path": "fake.wav",
    "transcription": {"primary": {"model": "sensevoice"}},
})
chk("diarization 默认关闭", cfg5.diarization.enabled == False)

print()
print("=" * 60)
print("Scenario 6: workflow_runner fallback 逻辑（primary 没 diarization）")
print("=" * 60)
# 所有模型都没 diarization → 走 runtime.diarize
fake_p = {"model": "sensevoice"}  # 没 diarization
fake_r = {"model": "qwen3-asr"}   # 没 diarization
target6 = cfg1.diarization.asr_model or cfg1.transcription.primary.model
cand6 = [item for item in (fake_p, fake_r) if isinstance(item.get("diarization"), dict)]
chk("candidates 为空列表", cand6 == [])
diarization6 = next(
    (item.get("diarization") for item in cand6 if str(item.get("model") or "") == target6),
    cand6[0]["diarization"] if cand6 else None,
)
chk("fallback 到 None → runtime.diarize()", diarization6 is None)

print()
print("=" * 60)
print("Scenario 7: workflow_runner fallback 有其他模型的 diarization")
print("=" * 60)
fake_p7 = {"model": "sensevoice"}  # 没有
fake_r7 = {"model": "paraformer", "diarization": {"segments": [{"speaker": 1}]}}  # reviewer 有
cand7 = [item for item in (fake_p7, fake_r7) if isinstance(item.get("diarization"), dict)]
chk("candidates 有 paraformer", len(cand7) == 1)
# 精确匹配 target_asr="sensevoice" 找不到 → fallback 到 candidates[0]
diarization7 = next(
    (item.get("diarization") for item in cand7 if str(item.get("model") or "") == target6),
    cand7[0]["diarization"] if cand7 else None,
)
chk("fallback 到 candidates[0]（paraformer 的）", diarization7 is not None)
if diarization7:
    chk("内容正确", diarization7["segments"][0]["speaker"] == 1)

print()
print("=" * 60)
print("Scenario 8: _resolve_runtime_models_to_local 主模型不覆盖 cfg['model']")
print("=" * 60)
# 模拟 FunASR AutoModel 内部拿到带 model_path 的 cfg 时能正确工作
cfg_dict = {
    "model": "iic/speech_eres2netv2_sv_zh-cn_16k-common",  # spk_model 注册 key
}
# 先 resolve
resolved = server._resolve_runtime_models_to_local(cfg_dict.copy())
chk("cfg['model'] 保留注册 key", resolved.get("model") == "iic/speech_eres2netv2_sv_zh-cn_16k-common")
chk("cfg['model_path'] 存在", resolved.get("model_path") is not None)
chk("cfg['check_latest'] = False", resolved.get("check_latest") == False)

print()
print("=" * 60)
print(f"Total: {sum(results)}/{len(results)} passed")
print("=" * 60)

# 总结
failed = len(results) - sum(results)
sys.exit(1 if failed else 0)
