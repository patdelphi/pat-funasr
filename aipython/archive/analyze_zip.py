# -*- coding: utf-8 -*-
"""
深度分析 ZIP 包内各类转录产物的格式、内容、质量问题。
直接跑：python -X utf8 aipython/analyze_zip.py
"""
import json, sys, re, os
from collections import Counter

sys.path.insert(0, 'app/openai_api')
sys.path.insert(0, 'app')

EXTRACT = os.path.join(os.environ.get('TEMP', '.'), 'pat-zip-inspect')
# 用 glob 找最新的时间戳
import glob
files = glob.glob(os.path.join(EXTRACT, '*.json'))
if not files:
    print('ZIP 还没解压到', EXTRACT)
    sys.exit(1)
ts = None
for f in files:
    m = re.search(r'(\d{8}_\d{6})', f)
    if m:
        ts = m.group(1)
        break
if not ts:
    print('找不到时间戳')
    sys.exit(1)

print(f'\n使用时间戳: {ts}')

# ============ 1) transcript.json ============
print('\n' + '=' * 60)
print('1. transcript.json')
print('=' * 60)
jpath = os.path.join(EXTRACT, f'transcript_{ts}.json')
raw = open(jpath, 'rb').read()
print(f'前 3 字节 (BOM 检测): {raw[:3]}')
# 有没有 BOM？
has_bom = raw.startswith(b'\xef\xbb\xbf')
print(f'有 BOM: {has_bom}')
print(f'能直接 json.loads: ', end='')
try:
    d = json.loads(raw)
    print('YES ✅')
except Exception as e:
    print(f'NO ❌ ({e})')
    d = json.loads(raw.decode('utf-8-sig'))

print(f'\n顶级 keys: {list(d.keys())}')
print(f'mode: {d.get("mode")}')
print(f'primary_model: {d.get("primary_model")}')
print(f'reviewer_models: {d.get("reviewer_models")}')
segs = d.get('segments', [])
print(f'segments 数: {len(segs)}')
print(f'text 长度: {len(d.get("text", ""))}')

all_keys = set()
for s in segs:
    all_keys.update(s.keys())
print(f'\n所有 segment 可能的 keys: {sorted(all_keys)}')

spk_counter = Counter(str(s.get('speaker', '<无>')) for s in segs)
print(f'speaker 分布: {dict(spk_counter)}')

empty = sum(1 for s in segs if not str(s.get('text', '')).strip())
print(f'空文本 segment: {empty}')

sorted_ok = all(segs[i].get('start', 0) <= segs[i + 1].get('start', 0)
                for i in range(len(segs) - 1))
print(f'segments 按 start 单调递增: {sorted_ok}')

overlap = sum(1 for i in range(1, len(segs))
              if segs[i].get('start', 0) < segs[i - 1].get('end', 0))
print(f'相邻 segment 时间重叠: {overlap}')

has_words = sum(1 for s in segs if s.get('words'))
print(f'有 words 级时间戳: {has_words}/{len(segs)}')

# 看看 model_runs 有没有暴露
print(f'\nmodel_runs 是否暴露在 payload 里: {"model_runs" in d}')

# 第一个和最后一个 segment 样例
if segs:
    print('\n--- 第一个 segment ---')
    s = segs[0]
    for k in ['start', 'end', 'speaker', 'text']:
        print(f'  {k}: {s.get(k)}')
    print('\n--- 最后一个 segment ---')
    s = segs[-1]
    for k in ['start', 'end', 'speaker', 'text']:
        print(f'  {k}: {s.get(k)}')

# ============ 2) transcript.txt ============
print('\n' + '=' * 60)
print('2. transcript.txt')
print('=' * 60)
tpath = os.path.join(EXTRACT, f'transcript_{ts}.txt')
raw = open(tpath, 'rb').read()
print(f'前 3 字节 (BOM): {raw[:3]}')
print(f'文件大小: {len(raw):,} bytes')
txt = raw.decode('utf-8-sig')
print(f'有 BOM (utf-8-sig 能读): YES')

lines = txt.splitlines()
print(f'总行数: {len(lines)}, 总字符: {len(txt):,}')
print(f'是否有 CRLF: {chr(13)+chr(10) in txt}')

spk_matches = re.findall(r'\[spk=(\d+)\]', txt)
print(f'[spk=N] 标签: {len(spk_matches)} 个, 唯一值: {sorted(set(spk_matches))}')

no_spk = sum(1 for l in lines if l.strip() and not l.strip().startswith('[spk='))
print(f'不带 [spk=] 标签的非空行: {no_spk} ← ⚠️ 有就是坏了')

blank = sum(1 for l in lines if not l.strip())
print(f'空行数（段落分隔）: {blank}')

print('\n--- 前 15 行 ---')
for i, l in enumerate(lines[:15], 1):
    print(f'  {i:>3}: {l[:90]}')

print('\n--- 中间 5 行 ---')
mid = len(lines) // 2
for i in range(max(0, mid - 2), min(len(lines), mid + 3)):
    print(f'  {i+1:>3}: {lines[i][:90]}')

# 检查有没有连续两个 [spk=N] 相同却没断开的段落（说明本应分段的没分）
print('\n--- speaker 切换频率 ---')
prev_spk = None
switches = 0
same_consec = 0
for l in lines:
    m = re.match(r'\[spk=(\d+)\]', l.strip())
    if m:
        spk = m.group(1)
        if prev_spk is not None and spk != prev_spk:
            switches += 1
        elif prev_spk is not None and spk == prev_spk:
            same_consec += 1
        prev_spk = spk
print(f'speaker 切换次数: {switches}')
print(f'连续同 speaker 段数（正常应该多）: {same_consec}')

# ============ 3) transcript.srt ============
print('\n' + '=' * 60)
print('3. transcript.srt')
print('=' * 60)
sp = os.path.join(EXTRACT, f'transcript_{ts}.srt')
raw = open(sp, 'rb').read()
print(f'前 3 字节 (BOM): {raw[:3]}')
print(f'文件大小: {len(raw):,} bytes')

srt = raw.decode('utf-8-sig')
blocks = [b.strip() for b in re.split(r'\n\s*\n', srt) if b.strip()]
print(f'字幕块数: {len(blocks)}')

time_ok = re.compile(r'^\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}$')
bad_time = 0
bad_blocks = []
for idx, b in enumerate(blocks):
    ls = b.split('\n')
    # SRT 标准: 编号 / 时间 / 文本 / 可选多行
    if not ls or not ls[0].strip().isdigit():
        # 有些 SRT 没有编号行
        if not time_ok.match(ls[0].strip()):
            bad_time += 1
            if len(bad_blocks) < 3:
                bad_blocks.append((idx, ls))
    elif len(ls) >= 2:
        if not time_ok.match(ls[1].strip()):
            bad_time += 1
            if len(bad_blocks) < 3:
                bad_blocks.append((idx, ls))

print(f'时间格式错误的块: {bad_time}')
if bad_blocks:
    for idx, ls in bad_blocks:
        print(f'  块 #{idx+1}: {ls[:2]}')

srt_spks = Counter()
for b in blocks:
    for m in re.finditer(r'\[spk=(\d+)\]', b):
        srt_spks[m.group(1)] += 1
print(f'speaker 标签: {dict(srt_spks)}')

# 检查字幕文本是否合理长度
lengths = []
for b in blocks:
    ls = b.split('\n')
    # 跳过编号+时间，取剩下的
    text_lines = [l for l in ls if not l.strip().isdigit()
                  and not time_ok.match(l.strip())
                  and l.strip()]
    text = ' '.join(text_lines)
    lengths.append(len(text))

if lengths:
    print(f'单块文本长度: min={min(lengths)}, max={max(lengths)}, avg={sum(lengths)/len(lengths):.1f}')
    long_blocks = sum(1 for x in lengths if x > 100)
    print(f'超长块(>100字符): {long_blocks}/{len(lengths)} ← 可能切分不均匀')

print('\n--- SRT 前 5 块 ---')
for i, b in enumerate(blocks[:5], 1):
    print(f'--- Block {i} ---')
    print(b[:200])
    print()

# ============ 4) VTT / TSV 是否存在 ============
print('=' * 60)
print('4. VTT / TSV / refined 文件')
print('=' * 60)
for ext in ['vtt', 'tsv']:
    p = os.path.join(EXTRACT, f'transcript_{ts}.{ext}')
    if os.path.exists(p):
        sz = os.path.getsize(p)
        print(f'transcript.{ext}: ✅ {sz:,} bytes')
        head = open(p, encoding='utf-8-sig').read()[:300]
        print(f'  前 200 字符: {head[:200]}')
    else:
        print(f'transcript.{ext}: ❌ 缺失')

refined = os.path.join(EXTRACT, f'transcript_refined_{ts}.txt')
if os.path.exists(refined):
    sz = os.path.getsize(refined)
    print(f'\ntranscript_refined.txt: ✅ {sz:,} bytes')
    content = open(refined, encoding='utf-8-sig').read()[:500]
    print(f'  前 500 字符: {content[:500]}')
else:
    print(f'\ntranscript_refined.txt: 不存在（正常，如果 LLM proofread 没改变全文的话不会生成）')

# ============ 5) mindmap.json ============
print('\n' + '=' * 60)
print('5. mindmap.json')
print('=' * 60)
mpath = os.path.join(EXTRACT, f'mindmap_{ts}.json')
if os.path.exists(mpath):
    md = json.loads(open(mpath, encoding='utf-8-sig').read())
    print(f'title: {md.get("title")}')
    children = md.get('children', [])
    print(f'children 数（顶层议题）: {len(children)}')
    total_nodes = 0
    def count(nodes):
        global total_nodes
        for n in nodes:
            total_nodes += 1
            count(n.get('children', []))
    count(children)
    print(f'总节点数: {total_nodes}')
    depths = []
    def depth(nodes, d):
        for n in nodes:
            depths.append(d)
            depth(n.get('children', []), d + 1)
    depth(children, 1)
    if depths:
        print(f'深度分布: {dict(Counter(depths))}')
    print('\n--- 前 3 个顶层议题 ---')
    for c in children[:3]:
        title = c.get('title', '')
        subcnt = len(c.get('children', []))
        print(f'  - {title} ({subcnt} 子节点)')
else:
    print('mindmap.json 不存在')

# ============ 6) summary.md 检查结构问题 ============
print('\n' + '=' * 60)
print('6. summary.md (我们的纪要) 结构问题')
print('=' * 60)
spath = os.path.join(EXTRACT, f'summary_{ts}.md')
if os.path.exists(spath):
    content = open(spath, encoding='utf-8-sig').read()
    lines = content.splitlines()
    print(f'总行数: {len(lines)}, 总字符: {len(content):,}')

    # 检查有没有 "第 N 部分" 这种分片标记（说明是旧的直拼逻辑没做二次聚合）
    chunk_markers = [l for l in lines if '第 ' in l and '部分' in l and '摘要' in l]
    print(f'分片标记 "摘要（第 N 部分）": {len(chunk_markers)} 处 ← {"❌ 这是旧结构没做二次聚合" if chunk_markers else "✅ 没有分片标记"}')

    # 检查有没有 list 形式的 JSON 裸输出
    json_dump_lines = [l for l in lines if l.strip().startswith('[') or l.strip().startswith("{'topic'") or "['topic'" in l]
    print(f'疑似 JSON 裸输出: {len(json_dump_lines)} 处 ← {"❌ 渲染逻辑没处理好" if json_dump_lines else "✅ 干净"}')
    if json_dump_lines:
        for l in json_dump_lines[:3]:
            print(f'  样例: {l[:80]}')

    # 检查"决定"/"行动项"是不是空的
    has_decision_section = any('决定' in l and l.strip().startswith('#') for l in lines)
    action_section = any('行动项' in l and l.strip().startswith('#') for l in lines)
    print(f'有"决定"章节: {has_decision_section}')
    print(f'有"行动项"章节: {action_section}')

    # 检查空的决定/行动项
    for i, l in enumerate(lines):
        if l.strip().startswith('### 决定') or l.strip().startswith('## 决定'):
            # 往后找有没有非空 bullet
            has_content = False
            for j in range(i + 1, min(i + 6, len(lines))):
                if lines[j].strip().startswith('-') and len(lines[j].strip()) > 2:
                    has_content = True
                    break
            if not has_content:
                print(f'  ⚠️ 第 {i+1} 行附近的"决定"章节是空的')
        if '行动项' in l and l.strip().startswith('#'):
            has_content = False
            for j in range(i + 1, min(i + 6, len(lines))):
                if '[ ]' in lines[j] or lines[j].strip().startswith('- '):
                    has_content = True
                    break
            if not has_content:
                print(f'  ⚠️ 第 {i+1} 行附近的"行动项"章节是空的')

    print('\n--- 前 30 行 ---')
    for i, l in enumerate(lines[:30], 1):
        print(f'  {i:>3}: {l[:90]}')
else:
    print('summary.md 不存在')

print('\n' + '=' * 60)
print('分析完成')
print('=' * 60)
