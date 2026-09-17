# -*- coding: utf-8 -*-
"""端到端验证：模拟真实 workflow，检查所有产物格式/内容/内部字段"""
import sys, json, tempfile, os
sys.path.insert(0, 'app/openai_api')
sys.path.insert(0, 'app/pat_funasr_webui/fine_transcription')

from openai_api import artifact_service, renderers

# ========== 构造带 speaker、带内部字段、带脏数据的 segments ==========
segments = [
    {'start': 0.0, 'end': 3.0, 'text': '好的，今天我们讨论一下项目进度', 'speaker': 0,
     'candidates': [1,2,3], 'decision_rule': 'majority', 'alignment_quality': 0.98},
    {'start': 3.0, 'end': 6.0, 'text': '首先看一下第一季度的数据表现', 'speaker': 0,
     'candidates': [1,2,3], 'decision_rule': 'majority', 'alignment_quality': 0.95},
    {'start': 6.0, 'end': 9.0, 'text': '我觉得整体还行但有几个问题', 'speaker': 1,
     'candidates': [1,2,3], 'decision_rule': 'majority', 'alignment_quality': 0.92},
    {'start': 9.0, 'end': 12.0, 'text': '对，我也注意到了那个 bug', 'speaker': 1,
     'candidates': [1,2,3], 'decision_rule': 'majority', 'alignment_quality': 0.90},
    {'start': 12.0, 'end': 15.0, 'text': '好，那我们讨论下怎么修', 'speaker': 0,
     'candidates': [1,2,3], 'decision_rule': 'majority', 'alignment_quality': 0.88},
]

result = {
    'text': '好的今天我们讨论一下项目进度...',
    'segments': segments,
    'refined_text': None,
    'summary': {
        'meeting_title': '项目进度讨论',
        'overall_summary': '讨论了 Q1 数据和遗留 bug',
        'sections': [
            {'topic': 'Q1 数据', 'summary': '整体还行', 'key_points': ['同比 +10%'],
             'decisions': [], 'action_items': [], 'risks_or_todos': []},
            {'topic': '遗留 bug', 'summary': '需要尽快修', 'key_points': [],
             'decisions': ['决定本周修'],
             'action_items': [{'task': '修 bug', 'owner': '张三', 'deadline': '周五'}],
             'risks_or_todos': []},
        ],
        'participants': ['张三', '李四'],
        'open_questions': [],
        'next_steps': ['周五前修完 bug'],
    },
    'mindmap': {'title': '会议导图', 'children': [{'title': 'Q1 数据', 'children': []}]},
    'model_runs': [{'internal': 'log'}],     # 顶级内部字段
    'original_text': '原始 ASR 输出',         # 顶级内部字段
}

failed = 0

def check(name, cond, detail=""):
    global failed
    if cond:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}  {detail}")
        failed += 1

print("=== 1. _public_result 内部字段清理 ===")
public = artifact_service._public_result(result, include_raw_candidates=False)
check("model_runs 被删", 'model_runs' not in public)
check("original_text 被删", 'original_text' not in public)
for seg in public['segments']:
    check("candidates 被删", 'candidates' not in seg)
    check("decision_rule 被删", 'decision_rule' not in seg)
    check("alignment_quality 被删", 'alignment_quality' not in seg)

print("\n=== 2. _public_result include_raw=True 保留 ===")
public_raw = artifact_service._public_result(result, include_raw_candidates=True)
check("model_runs 保留", 'model_runs' in public_raw)
check("segments[0].candidates 保留", 'candidates' in public_raw['segments'][0])

print("\n=== 3. _clean_segment_text + render_txt speaker 合并 ===")
dirty_seg = {'text': '第一句\n\n第二句', 'speaker': 0}
cleaned = renderers._clean_segment_text(dirty_seg)
check("清理后无换行", '\n' not in cleaned, f"got: {repr(cleaned)}")

txt = renderers.render_txt(segments)
lines = [l for l in txt.split('\n') if l.strip()]
check(f"speaker 合并后 3 段（实际 {len(lines)}）", len(lines) == 3, f"lines={lines}")
if len(lines) >= 3:
    check("第 1 段 [spk=0]", lines[0].startswith('[spk=0]'))
    check("第 2 段 [spk=1]", lines[1].startswith('[spk=1]'))
    check("第 3 段 [spk=0]", lines[2].startswith('[spk=0]'))

print("\n=== 4. write_workflow_artifacts 端到端 ===")
with tempfile.TemporaryDirectory() as tmpdir:
    artifact_service.write_workflow_artifacts(
        public, job_id='test_job_001', output_dir=tmpdir,
        formats=['json', 'txt', 'srt', 'vtt', 'tsv', 'all'],
    )
    files = sorted([f for f in os.listdir(tmpdir) if f != 'test_job_001.zip'])
    print(f"  输出 {len(files)} 个文件:")
    for f in files:
        size = os.path.getsize(os.path.join(tmpdir, f))
        print(f"    {f}  ({size} bytes)")

    # JSON 检查
    json_files = [f for f in files if f.endswith('.json') and 'transcript' in f.lower() and 'refined' not in f.lower()]
    check("至少 1 个 transcript JSON", len(json_files) >= 1)
    if json_files:
        jp = os.path.join(tmpdir, json_files[0])
        with open(jp, 'rb') as fh:
            head = fh.read(3)
        check("JSON 无 BOM", head != b'\xef\xbb\xbf')
        try:
            json.loads(open(jp, encoding='utf-8').read())
            check("JSON 能 json.loads", True)
        except Exception as e:
            check("JSON 能 json.loads", False, str(e))

    # SRT 检查
    srt_files = [f for f in files if f.endswith('.srt')]
    check("至少 1 个 SRT", len(srt_files) >= 1)
    if srt_files:
        sp = os.path.join(tmpdir, srt_files[0])
        srt_text = open(sp, encoding='utf-8').read()
        blocks = [b.strip() for b in srt_text.split('\n\n') if b.strip()]
        bad_blocks = []
        for block in blocks:
            blines = [l for l in block.split('\n') if l.strip()]
            if len(blines) < 3:
                bad_blocks.append(f"不够3行: {block[:60]}")
            elif '-->' not in blines[1]:
                bad_blocks.append(f"缺箭头: {block[:60]}")
        check(f"SRT {len(blocks)} 块格式合法", len(bad_blocks) == 0, str(bad_blocks))

    # TXT 检查
    txt_files = [f for f in files if f.endswith('.txt') and 'refined' not in f.lower()]
    check("至少 1 个 transcript TXT", len(txt_files) >= 1)
    if txt_files:
        tp = os.path.join(tmpdir, txt_files[0])
        tc = open(tp, encoding='utf-8').read()
        check("TXT 有 [spk=0]", '[spk=0]' in tc)
        check("TXT 有 [spk=1]", '[spk=1]' in tc)

    # VTT/TSV 存在检查
    vtt_exists = any(f.endswith('.vtt') for f in files)
    tsv_exists = any(f.endswith('.tsv') for f in files)
    check("VTT 存在", vtt_exists)
    check("TSV 存在", tsv_exists)

print("\n=== 5. _ts_stem 正则 ===")
from openai_api.artifact_service import _ts_stem
check("transcript_20260903_180245.json → ('transcript', 'json')",
      _ts_stem('transcript_20260903_180245.json') == ('transcript', 'json'))
check("transcript_refined_20260903_180245.txt → ('transcript_refined', 'txt')",
      _ts_stem('transcript_refined_20260903_180245.txt') == ('transcript_refined', 'txt'))
check("summary_20260903_180245.md → ('summary', 'md')",
      _ts_stem('summary_20260903_180245.md') == ('summary', 'md'))
check("plain_old.json → None", _ts_stem('plain_old.json') is None)

# 总结
print(f"\n{'='*50}")
if failed == 0:
    print("🎉 全部端到端验证通过！")
else:
    print(f"💥 有 {failed} 项检查失败！")
    sys.exit(1)
