# -*- coding: utf-8 -*-
"""Review: 深挖 _workflow_transcribe_model 的 diarization 完整链路"""
import sys, inspect
sys.path.insert(0, 'app/openai_api')
from openai_api.server import _workflow_transcribe_model
src = inspect.getsource(_workflow_transcribe_model)
lines = src.split('\n')
for i, l in enumerate(lines):
    key = False
    for k in ['spk_model', 'speaker_model', 'diarization', 'reuse_for_diarization', 'output[', 'spk_embedding']:
        if k in l:
            key = True; break
    if key:
        start = max(0, i-3); end = min(len(lines), i+8)
        print(f'--- L{i+1} ---')
        for j in range(start, end):
            print(f'  {j+1}: {lines[j][:220]}')
        print()
