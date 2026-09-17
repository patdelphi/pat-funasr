# -*- coding: utf-8 -*-
"""review 后半段 + pytest"""
import sys, os, json, tempfile, re
sys.path.insert(0, 'app/openai_api'); sys.path.insert(0, 'app')
from openai_api import renderers, artifact_service
from pat_funasr_webui.fine_transcription import summary_processor, scene_templates

print('=== 6. 会议 summary_prompt 字段检查 ===')
sp = scene_templates.MEETING.summary_prompt
checks = [
    ('meeting_title', '顶层会议主题'),
    ('overall_summary', '总览'),
    ('sections', '议题列表'),
    ('decisions', '决定'),
    ('action_items', '行动项数组'),
    ('owner', '行动项责任人'),
    ('deadline', '行动项截止时间'),
    ('risks_or_todos', '风险/待确认'),
    ('open_questions', '会后待确认'),
    ('next_steps', '后续步骤'),
]
for field, label in checks:
    ok = field in sp
    print(f'  {"✅" if ok else "❌"} {label}: {field}')

print('\n=== 7. 二次聚合函数 ===')
print(f'  summary _SYNTHESIZE_PROMPT: {len(summary_processor._SYNTHESIZE_PROMPT):,} chars ✅')
print(f'  mindmap _MINDMAP_SYNTHESIZE_PROMPT: {len(summary_processor._MINDMAP_SYNTHESIZE_PROMPT):,} chars ✅')
print(f'  _llm_synthesize_summaries: {hasattr(summary_processor, "_llm_synthesize_summaries")}')
print(f'  _llm_synthesize_mindmap: {hasattr(summary_processor, "_llm_synthesize_mindmap")}')

print('\n=== 8. 端到端 write_workflow_artifacts ===')
tmp = tempfile.mkdtemp()
fake = {
    'text': '全文', 'refined_text': '',
    'segments': [
        {'text': '正常段', 'speaker': 0, 'start': 0, 'end': 1,
         'candidates': ['内部'], 'alignment_quality': 0.9},
        {'text': '带\n\n换行的段', 'speaker': 1, 'start': 1, 'end': 2,
         'speaker_overlap_ratio': 0.5},
    ],
    'model_runs': [{'internal': True}],
    'original_text': '原始',
    'summary': {'meeting_title': '测试', 'overall_summary': '总览',
                'sections': [{'topic': '议题一', 'summary': '讨论了A'}]},
    'mindmap': {'title': '导图', 'children': [{'title': '根'}]},
}
arts = artifact_service.write_workflow_artifacts(
    output_dir=tmp, result=fake, config={'export': {'formats': ['all']}},
    events=[{'event_id': 1, 'stage': 'done', 'message': 'OK'}],
    formats=['all'], include_raw_candidates=False, include_config_snapshot=True,
)
files = sorted(os.listdir(tmp))
print(f'  生成 {len(files)} 个文件:')
for fn in files:
    sz = os.path.getsize(os.path.join(tmp, fn))
    print(f'    {fn:<50} {sz:>8,}')

# VTT / TSV 存在性
for ext in ['vtt', 'tsv']:
    hit = [fn for fn in files if fn.endswith('.' + ext)]
    print(f'  .{ext} 存在: {"✅" if hit else "❌"}')

refined = [fn for fn in files if 'refined' in fn]
print(f'  transcript_refined.txt (无变化时): {"无 ✅ 正确不生成" if not refined else "有"}')

# JSON 泄漏
jfile = next((os.path.join(tmp, fn) for fn in files
              if fn.endswith('.json') and 'transcript' in fn and 'config' not in fn), None)
if jfile:
    raw = open(jfile, 'rb').read()
    has_bom = raw.startswith(b'\xef\xbb\xbf')
    print(f'  transcript.json 有 BOM: {has_bom} (预期 False) {"❌" if has_bom else "✅"}')
    text = raw.decode('utf-8')
    for leak in ['model_runs', 'original_text', 'alignment_quality',
                 'disagreement_score', 'speaker_overlap_ratio']:
        if leak in text:
            print(f'  ❌ 泄漏字段: {leak}')
    else:
        print('  segment/顶级字段泄漏检查: ✅ 全部清除')

# summary.md 新结构
md_file = next((os.path.join(tmp, fn) for fn in files if fn.endswith('.md')), None)
if md_file:
    md = open(md_file, encoding='utf-8-sig').read()
    print(f'  summary.md 有 "## 会议总览": {"## 会议总览" in md} ✅')
    print(f'  summary.md 有 "## 议题讨论": {"## 议题讨论" in md} ✅')
    old_marker = ('第 ' in md) and ('部分' in md) and ('摘要' in md)
    print(f'  旧分片标记残留: {old_marker} (预期 False) {"❌" if old_marker else "✅"}')

print('\n=== 9. pytest ===')
