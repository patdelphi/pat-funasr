# -*- coding: utf-8 -*-
"""全量 review 脚本：逐模块测试所有改动"""
import sys, os, re, json
sys.path.insert(0, 'app/openai_api')
sys.path.insert(0, 'app')

print('=' * 60)
print('1. 模块导入')
print('=' * 60)
from openai_api import renderers, artifact_service
from pat_funasr_webui.fine_transcription import summary_processor, scene_templates
print('  ✅ renderers')
print('  ✅ artifact_service')
print('  ✅ summary_processor')
print('  ✅ scene_templates')

print('\n' + '=' * 60)
print('2. _clean_segment_text 边界')
print('=' * 60)
from openai_api.renderers import _clean_segment_text

cases = [
    ('正常文本', '正常文本'),
    ('带\n换行', '带 换行'),
    ('带\n\n双换行', '带 双换行'),
    ('带\r\nCRLF', '带 CRLF'),
    ('多空格  在这里', '多空格 在这里'),
    ('  前后空格  ', '前后空格'),
    ('', ''),
    (None, ''),
]
all_ok = True
for inp, expected in cases:
    out = _clean_segment_text({'text': inp})
    ok = out == expected
    if not ok:
        all_ok = False
        print(f'  ❌ input={inp!r}  expected={expected!r}  got={out!r}')
if all_ok:
    print('  ✅ 全部 8 个边界通过')

print('\n' + '=' * 60)
print('3. render_txt speaker 合并')
print('=' * 60)
from openai_api.renderers import render_txt

fake_segs = [
    {'text': '你好。', 'speaker': 'A', 'start': 0, 'end': 1},
    {'text': '世界。', 'speaker': 'A', 'start': 1, 'end': 2},
    {'text': '嗯对。', 'speaker': 'B', 'start': 2, 'end': 3},
    {'text': '你好吗。', 'speaker': 'A', 'start': 3, 'end': 4},
    {'text': '好的。', 'speaker': 'A', 'start': 4, 'end': 5},
    {'text': '',      'speaker': 'A', 'start': 5, 'end': 6},  # 跳过
    {'text': None,    'speaker': 'A', 'start': 6, 'end': 7},  # 跳过
]
result = render_txt(fake_segs)
parts = [p for p in result.split('\n\n') if p.strip()]
print(f'  段数: {len(parts)} (预期 3)')
if len(parts) != 3:
    print('  ❌ 段数不对！')
    print(result)
else:
    print('  ✅ 段数正确')

# 检查 speaker 顺序
spk_order = re.findall(r'\[spk=(\w+)\]', result)
print(f'  speaker 出现顺序: {spk_order} (预期: A, B, A)')
if spk_order == ['A', 'B', 'A']:
    print('  ✅ speaker 分段正确')
else:
    print('  ❌ 不对')

# 检查同 A 的两段合并了
if '你好。 世界。' in result:
    print('  ✅ 同 speaker 文本合并正确')
else:
    print('  ❌ 没合并？')
    print(result)

print('\n' + '=' * 60)
print('4. render_srt 孤儿行防线')
print('=' * 60)
from openai_api.renderers import render_srt

# 模拟有 \n 的坏 segment
bad_segs = [
    {'text': '正常第一段', 'speaker': 1, 'start': 0, 'end': 1},
    # 这就是原来产生孤儿行的那种 seg —— LLM proofread 留下了 \n\n
    {'text': '上一段。\n\n好，然后再回到咱们', 'speaker': 1, 'start': 1, 'end': 2},
    {'text': '',           'speaker': 1, 'start': 2, 'end': 3},  # 空的应跳过
    {'text': None,         'speaker': 1, 'start': 3, 'end': 4},  # None 跳过
]
srt_out = render_srt(bad_segs)
lines = srt_out.split('\n')
time_re = re.compile(r'^\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}$')
bad_lines = []
for i, l in enumerate(lines):
    stripped = l.strip()
    if not stripped:
        continue
    if stripped.isdigit() or time_re.match(stripped) or stripped.startswith('[spk='):
        continue
    bad_lines.append((i + 1, stripped[:60]))
if bad_lines:
    print(f'  ❌ 发现坏行: {bad_lines}')
    print(srt_out)
else:
    print('  ✅ SRT 结构干净，没有孤儿行')

# 确认 \n 被正确清除成了空格
if '好，然后再回到咱们' in srt_out and '\n\n好' not in srt_out:
    print('  ✅ segment 内部 \\n 被正确合并成空格')

# 检查空 seg 是否真的跳过了
blocks = [b for b in re.split(r'\n\s*\n', srt_out) if b.strip()]
print(f'  字幕块数: {len(blocks)} (预期 2 — 空 seg 被跳过)')
if len(blocks) == 2:
    print('  ✅ 空 seg 被正确跳过')
else:
    print('  ❌ 空 seg 没跳过？')

print('\n' + '=' * 60)
print('5. _public_result 字段清理')
print('=' * 60)
fake = {
    'text': '全文',
    'segments': [{
        'text': 'hi', 'speaker': 0, 'start': 0, 'end': 1,
        'candidates': [], 'alternatives': [], 'alignment_quality': 0.9,
        'decision_rule': 'concat', 'disagreement_score': 0.1,
        'selected_models': ['a'], 'speaker_candidates': ['spk0'],
        'speaker_overlap_ratio': 0.0, 'speaker_uncertain': False, 'uncertain': False,
    }],
    'model_runs': [{'log': '内部'}],
    'original_text': '原始',
    'refined_text': '润色',
    'summary': {'meeting_title': '测试'},
    'artifacts': [],
}
clean = artifact_service._public_result(fake, False)
top_leak = [k for k in ['model_runs', 'original_text', 'artifacts'] if k in clean]
seg_leak = [k for k in ['candidates', 'alternatives', 'alignment_quality',
                         'decision_rule', 'disagreement_score', 'selected_models',
                         'speaker_candidates', 'speaker_overlap_ratio',
                         'speaker_uncertain', 'uncertain']
            if k in clean['segments'][0]]
print(f'  顶级泄漏: {top_leak or "无 ✅"}')
print(f'  segment 泄漏: {seg_leak or "无 ✅"}')

# include_raw_candidates=True 时保留
verbose = artifact_service._public_result(fake, True)
print(f'  include_raw=True 时 model_runs 保留: {"model_runs" in verbose} ✅')
print(f'  include_raw=True 时 candidates 保留: {"candidates" in verbose["segments"][0]} ✅')

print('\n' + '=' * 60)
print('6. summary_prompt 结构')
print('=' * 60)
tpl = scene_templates.SCENE_TEMPLATES['meeting']
sp = tpl.summary_prompt
print(f'  含 meeting_title: {"meeting_title" in sp}')
print(f'  含 overall_summary: {"overall_summary" in sp}')
print(f'  含 sections: {"sections" in sp}')
print(f'  含 action_items 字典结构: {"owner" in sp and "deadline" in sp}')

print('\n' + '=' * 60)
print('7. _SYNTHESIZE_PROMPT 和 _MINDMAP_SYNTHESIZE_PROMPT')
print('=' * 60)
print(f'  summary 聚合 prompt 长度: {len(summary_processor._SYNTHESIZE_PROMPT):,} chars')
print(f'  mindmap 聚合 prompt 长度: {len(summary_processor._MINDMAP_SYNTHESIZE_PROMPT):,} chars')
print(f'  存在 _llm_synthesize_summaries: {hasattr(summary_processor, "_llm_synthesize_summaries")}')
print(f'  存在 _llm_synthesize_mindmap: {hasattr(summary_processor, "_llm_synthesize_mindmap")}')

print('\n' + '=' * 60)
print('8. 端到端模拟：走完整 write_workflow_artifacts 流程')
print('=' * 60)
import tempfile
tmp = tempfile.mkdtemp()
fake_full = {
    'text': '全文。\n第二段。',
    'segments': [
        {'text': '你好。', 'speaker': 0, 'start': 0, 'end': 1,
         'candidates': ['内部'], 'alignment_quality': 0.9},
        {'text': '世界。', 'speaker': 0, 'start': 1, 'end': 2},
        {'text': '嗯。',  'speaker': 1, 'start': 2, 'end': 3,
         'text': '嗯。\n\n好',  # 故意留 \n
         'candidates': ['内部'],
         'speaker_overlap_ratio': 0.5},
    ],
    'model_runs': [{'internal': True}],
    'original_text': '原始',
    'refined_text': '',  # 没改过，不生成 transcript_refined.txt
    'summary': {
        'meeting_title': '测试会议',
        'overall_summary': '这是总览。',
        'sections': [
            {'topic': '议题一', 'summary': '讨论了A。', 'decisions': ['决定X'],
             'action_items': [{'task': '做Y', 'owner': '张三', 'deadline': '周五'}]},
        ],
    },
    'mindmap': {'title': '测试导图', 'children': [{'title': '根'}]},
}
artifacts = artifact_service.write_workflow_artifacts(
    output_dir=tmp,
    result=fake_full,
    config={'export': {'formats': ['all']}},
    events=[{'event_id': 1, 'stage': 'done', 'message': 'OK'}],
    formats=['all'],
    include_raw_candidates=False,
    include_config_snapshot=True,
)
print(f'  生成文件数: {len(artifacts)}')
files = os.listdir(tmp)
for f in sorted(files):
    sz = os.path.getsize(os.path.join(tmp, f))
    print(f'    {f:<50} {sz:>8,} bytes')

# 检查 JSON 里有没有泄漏
for f in files:
    if f.endswith('.json') and 'transcript' in f:
        raw = open(os.path.join(tmp, f), 'rb').read()
        # 应该没有 BOM
        has_bom = raw.startswith(b'\xef\xbb\xbf')
        print(f'  {f}: has BOM={has_bom} (预期 False)')
        # 内部字段不应出现
        text = raw.decode('utf-8')
        for leak in ['model_runs', 'original_text', 'alignment_quality',
                      'disagreement_score', 'speaker_uncertain']:
            if leak in text:
                print(f'    ❌ 泄漏字段: {leak}')

# 检查 SRT 里的 \n 被正确清除了
for f in files:
    if f.endswith('.srt'):
        content = open(os.path.join(tmp, f), encoding='utf-8-sig').read()
        # 不应该有孤儿行
        lines = content.split('\n')
        bad = 0
        for l in lines:
            s = l.strip()
            if s and not s.isdigit() and not time_re.match(s) and not s.startswith('[spk='):
                bad += 1
        print(f'  {f}: 孤儿行数={bad} (预期 0)')
        # 原来的 \n\n 应该变成空格
        if '\n\n好' not in content:
            print('    ✅ 内部 \\n 被清除')

print('\n' + '=' * 60)
print('REVIEW 完成')
print('=' * 60)
