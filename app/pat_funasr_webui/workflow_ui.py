"""
程序说明：
精细转录工作流前端适配器，负责配置组装和实时事件文本渲染。
"""

from __future__ import annotations

import html
from typing import Any


def _llm_stage(
    enabled: bool,
    selection: str | None,
    *,
    scope: str,
    template_id: str,
    preserve_timestamps: bool = True,
    preserve_speakers: bool = True,
) -> dict[str, Any]:
    value = str(selection or "")
    profile_id, model = (value.split("|", 1) + [""])[:2] if "|" in value else (value, "")
    return {
        "enabled": bool(enabled),
        "provider_profile_id": profile_id,
        "model": model,
        "scope": scope,
        "template_id": template_id,
        "preserve_timestamps": bool(preserve_timestamps),
        "preserve_speakers": bool(preserve_speakers),
    }


def build_workflow_config(values: dict[str, Any]) -> dict[str, Any]:
    """把前端字段映射为后端严格 workflow schema，所有选择均显式保留。"""
    reviewer_models = list(values.get("reviewer_models") or [])
    transcription_mode = str(values.get("transcription_mode") or "single_model")
    if transcription_mode == "single_model":
        reviewer_models = []
    primary_model = str(values.get("primary_model") or "sensevoice")
    primary_weight = float(values.get("primary_weight") or 1.0)
    reviewer_weight = float(values.get("reviewer_weight") or 1.0)
    return {
        "workflow_version": "1.0",
        "preset_id": str(values.get("preset_id") or "custom"),
        "preprocess": {
            "enabled": bool(values.get("preprocess_enabled", False)),
            "noise_reduction": bool(values.get("noise_reduction", False)),
            "noise_strength": float(values.get("noise_strength") or 8.0),
            "sample_rate": int(values.get("sample_rate") or 16000),
            "loudnorm": bool(values.get("loudnorm", True)),
            "silence_mode": str(values.get("silence_mode") or "preserve_timeline"),
        },
        "segmentation": {
            "vad_enabled": bool(values.get("vad_enabled", True)),
            "vad_preset": str(values.get("vad_preset") or "default"),
            "chunk_enabled": bool(values.get("chunk_enabled", True)),
            "chunk_seconds": int(values.get("chunk_seconds") or 240),
            "overlap_seconds": int(values.get("overlap_seconds") or 10),
        },
        "transcription": {
            "mode": transcription_mode,
            "primary": {
                "model": primary_model,
                "weight": primary_weight,
                "language": str(values.get("language") or "auto"),
                "hotword": str(values.get("hotword") or ""),
                "use_itn": values.get("use_itn"),
                "punc_mode": str(values.get("punc_mode") or "auto"),
            },
            "reviewers": [
                {
                    "model": model,
                    "weight": reviewer_weight,
                    "language": str(values.get("language") or "auto"),
                    "hotword": str(values.get("hotword") or ""),
                    "use_itn": values.get("use_itn"),
                    "punc_mode": str(values.get("punc_mode") or "auto"),
                }
                for model in reviewer_models
                if model and model != primary_model
            ],
            "execution": str(values.get("execution") or "parallel"),
            "max_concurrency": int(values.get("max_concurrency") or 1),
            "resource_failure_policy": str(values.get("resource_failure_policy") or "stop_and_ask"),
        },
        "timestamps": {
            "level": str(values.get("timestamp_level") or "segment"),
            "forced_alignment": bool(values.get("forced_alignment", False)),
            "aligner_model": str(values.get("aligner_model") or ""),
        },
        "diarization": {
            "enabled": bool(values.get("diarization_enabled", False)),
            "strategy": str(values.get("diarization_strategy") or "separate_align"),
            "asr_model": str(values.get("diarization_asr_model") or "paraformer"),
            "speaker_model": str(values.get("speaker_model") or "cam++"),
            "spk_mode": str(values.get("spk_mode") or "punc_segment"),
            "preset_speaker_count": values.get("preset_speaker_count"),
            "global_speaker_clustering": bool(values.get("global_speaker_clustering", True)),
        },
        "reconciliation": {
            "mode": str(values.get("reconciliation_mode") or "primary_first"),
            "disagreement_threshold": float(values.get("disagreement_threshold") or 0.2),
            "keep_alternatives": bool(values.get("keep_alternatives", True)),
            "uncertain_policy": str(values.get("uncertain_policy") or "flag_for_review"),
        },
        "llm_proofread": _llm_stage(
            bool(values.get("llm_proofread_enabled", False)),
            values.get("llm_proofread_selection"),
            # scope=segments 会逐段调 LLM，2600+ 段需 45 分钟；
            # scope=refined 全文拼接后内部按 6000 字/块流式处理，3-5 分钟完成。
            scope=str(values.get("llm_proofread_scope") or "refined"),
            template_id=str(values.get("llm_proofread_template_id") or "default"),
            preserve_timestamps=bool(values.get("llm_proofread_preserve_timestamps", True)),
            preserve_speakers=bool(values.get("llm_proofread_preserve_speakers", True)),
        ),
        "summary": _llm_stage(
            bool(values.get("summary_enabled", False)),
            values.get("summary_selection"),
            scope=str(values.get("summary_scope") or "refined"),
            template_id=str(values.get("summary_template_id") or "meeting"),
        ),
        "mindmap": _llm_stage(
            bool(values.get("mindmap_enabled", False)),
            values.get("mindmap_selection"),
            scope=str(values.get("mindmap_scope") or "refined"),
            template_id=str(values.get("mindmap_template_id") or "meeting"),
        ),
        "translation": {
            "enabled": bool(values.get("translation_enabled", False)),
            "model": str(values.get("translation_model") or "nllb-200-distilled-600m"),
            "source_lang": str(values.get("source_lang") or ""),
            "target_lang": str(values.get("target_lang") or ""),
        },
        "emotion": {
            "enabled": bool(values.get("emotion_enabled", False)),
            "model": str(values.get("emotion_model") or "emotion2vec-plus-large"),
            "granularity": str(values.get("emotion_granularity") or "utterance"),
        },
        "export": {
            "formats": list(values.get("export_formats") or ["json", "txt"]),
            "include_raw_candidates": bool(values.get("include_raw_candidates", False)),
            "include_config_snapshot": bool(values.get("include_config_snapshot", True)),
        },
    }


def render_workflow_events(events: list[dict[str, Any]]) -> str:
    """将追加式状态事件渲染为可复制日志，保留 warning/error 和错误码。"""
    lines: list[str] = []
    for event in events:
        progress = event.get("progress")
        progress_text = f"[{int(float(progress) * 100):02d}%]" if progress is not None else "[--%]"
        level = str(event.get("level") or "info").upper()
        stage = str(event.get("stage") or "workflow")
        model = f" [{event['model']}]" if event.get("model") else ""
        code = f" ({event['error_code']})" if event.get("error_code") else ""
        lines.append(
            f"#{event.get('event_id', '-')} {progress_text} {level:<8} {stage}{model}: "
            f"{event.get('message', '')}{code}"
        )
    return "\n".join(lines)


def render_workflow_event_panel(
    events: list[dict[str, Any]],
    warnings: list[str] | None = None,
) -> str:
    """渲染可暂停滚动、筛选、复制和下载的实时事件面板。"""
    rows: list[str] = []
    for warning in warnings or []:
        rows.append(
            '<div class="pat-workflow-event" data-level="warning">'
            f"{html.escape(str(warning))}</div>"
        )
    for event in events:
        level = str(event.get("level") or "info").lower()
        text = render_workflow_events([event])
        rows.append(
            f'<div class="pat-workflow-event" data-level="{html.escape(level)}">'
            f"{html.escape(text)}</div>"
        )
    content = "".join(rows) or '<div class="pat-workflow-empty">等待任务事件...</div>'
    return f"""
<div id="pat-workflow-event-panel">
  <div class="pat-workflow-toolbar">
    <button type="button" onclick="window.__patWorkflowLog.togglePause()">暂停/继续滚动</button>
    <button type="button" onclick="window.__patWorkflowLog.toggleErrors()">仅警告/错误</button>
    <button type="button" onclick="window.__patWorkflowLog.copy()">复制</button>
    <button type="button" onclick="window.__patWorkflowLog.download()">下载日志</button>
    <span id="pat-workflow-log-mode"></span>
  </div>
  <div id="pat-workflow-event-scroll">{content}</div>
</div>
<style>
#pat-workflow-event-panel{{border:1px solid #d8d8e5;border-radius:8px;background:#111827;color:#e5e7eb}}
.pat-workflow-toolbar{{display:flex;gap:6px;align-items:center;padding:8px;border-bottom:1px solid #374151;flex-wrap:wrap}}
.pat-workflow-toolbar button{{font-size:12px;padding:4px 8px;border-radius:5px;background:#374151;color:#fff;border:0;cursor:pointer}}
#pat-workflow-event-scroll{{height:300px;overflow:auto;padding:8px;font:12px/1.55 ui-monospace,Consolas,monospace;white-space:pre-wrap}}
.pat-workflow-event{{padding:2px 0;border-bottom:1px solid #1f2937}}
.pat-workflow-event[data-level="warning"]{{color:#fbbf24}}
.pat-workflow-event[data-level="error"]{{color:#f87171}}
.pat-workflow-empty{{color:#9ca3af}}
</style>
<script>
(function(){{
  const state = window.__patWorkflowLogState || (window.__patWorkflowLogState={{paused:false,errorsOnly:false}});
  function apply(){{
    const box=document.getElementById('pat-workflow-event-scroll');
    if(!box)return;
    box.querySelectorAll('.pat-workflow-event').forEach(row=>{{
      const level=row.dataset.level;
      row.style.display=(!state.errorsOnly||level==='warning'||level==='error')?'':'none';
    }});
    const mode=document.getElementById('pat-workflow-log-mode');
    if(mode)mode.textContent=(state.paused?'已暂停滚动':'自动滚动')+' / '+(state.errorsOnly?'仅异常':'全部级别');
    if(!state.paused)box.scrollTop=box.scrollHeight;
  }}
  window.__patWorkflowLog={{
    togglePause(){{state.paused=!state.paused;apply();}},
    toggleErrors(){{state.errorsOnly=!state.errorsOnly;apply();}},
    copy(){{const box=document.getElementById('pat-workflow-event-scroll');if(box)navigator.clipboard.writeText(box.innerText);}},
    download(){{const box=document.getElementById('pat-workflow-event-scroll');if(!box)return;const blob=new Blob([box.innerText],{{type:'text/plain;charset=utf-8'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='workflow-events.log';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}}
  }};
  apply();
}})();
</script>
""".strip()
