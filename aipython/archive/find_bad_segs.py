# -*- coding: utf-8 -*-
"""定位 JSON segments 中产生 SRT 孤儿行的坏 segment"""
import json, os

extract = os.path.join(os.environ.get('TEMP', '.'), 'pat-zip-inspect')
raw = open(os.path.join(extract, 'transcript_20260914_212503.json'), encoding='utf-8-sig').read()
d = json.loads(raw)
segs = d['segments']

# 找文本含 "好，然后再回到咱们" 或 "我觉得你们从" 的 segment
orphan_texts = ['好，然后再回到咱们', '我觉得你们从']
for target in orphan_texts:
    print(f'\n=== 找含 "{target}" 的 segments ===')
    for i, seg in enumerate(segs):
        text = seg.get('text', '')
        if target in text:
            print(f'  seg[{i}] start={seg.get("start")} end={seg.get("end")} '
                  f'speaker={seg.get("speaker")} text={text!r}')

# 看看 00:38:57 ~ 00:39:00 和 00:46:00 ~ 00:46:02 范围有多少 segment（重叠问题）
print('\n=== 38:55 ~ 39:05 范围 segments ===')
for i, seg in enumerate(segs):
    s = seg.get('start', 0)
    e = seg.get('end', 0)
    if 38*60+55 <= s <= 39*60+5:
        print(f'  [{i}] {s:.3f}->{e:.3f} spk={seg.get("speaker")} '
              f'len={len(seg.get("text",""))} text={seg.get("text","")[:50]!r}')

print('\n=== 46:00 ~ 46:10 范围 segments ===')
for i, seg in enumerate(segs):
    s = seg.get('start', 0)
    e = seg.get('end', 0)
    if 46*60 <= s <= 46*60+10:
        print(f'  [{i}] {s:.3f}->{e:.3f} spk={seg.get("speaker")} '
              f'len={len(seg.get("text",""))} text={seg.get("text","")[:50]!r}')
