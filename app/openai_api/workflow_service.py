"""
程序说明：
精细转录工作流配置、任务队列和实时状态事件服务。

职责：
- 解析并校验前端显式 workflow 配置。
- 使用受控线程池执行长任务，不阻塞 API 事件循环。
- 维护追加式事件日志、单调进度、取消和任务快照。

说明：
当前使用进程内任务存储，避免未经确认修改 SQLite；进程重启后任务不会恢复。
"""

from __future__ import annotations

import copy
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


def _safe_error_message(error: Exception) -> str:
    """脱敏异常中的绝对路径、认证头和常见密钥字段。"""
    message = str(error) or error.__class__.__name__
    message = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+", "Bearer [REDACTED]", message)
    message = re.sub(
        r"(?i)(api[_-]?key|token|authorization|cookie)\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        message,
    )
    message = re.sub(r"[A-Za-z]:\\[^\r\n,;]+", "[REDACTED_PATH]", message)
    message = re.sub(r"(?<![A-Za-z0-9])/(?:[^\s,;]+/)*[^\s,;]+", "[REDACTED_PATH]", message)
    return message[:1000]


def _format_duration(seconds: float) -> str:
    """把秒数格式化为人可读时长：12.3s / 2m15s / 1h30m05s。"""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s"


def _build_summary_message(
    created_at_iso: str,
    now_iso: str,
    *,
    status_label: str,
    stage_timings: list[dict[str, Any]] | None = None,
) -> str:
    """生成统一格式的任务汇总：开始时间 / 结束时间 / 耗时 + 分阶段耗时。"""
    try:
        start = datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
        end = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    except Exception:
        return f"[{status_label}] 任务结束"
    local_fmt = "%H:%M:%S"
    start_str = start.astimezone().strftime(local_fmt)
    end_str = end.astimezone().strftime(local_fmt)
    elapsed = (end - start).total_seconds()
    header = f"[{status_label}] 开始 {start_str} → 结束 {end_str}，共耗时 {_format_duration(elapsed)}"
    # 分阶段耗时
    if stage_timings:
        parts: list[str] = []
        for t in stage_timings:
            stage = t.get("stage", "?")
            model = t.get("model", "")
            dur = _format_duration(t.get("duration_s", 0))
            label = f"{stage}[{model}]" if model else stage
            parts.append(f"{label}={dur}")
        # 合并同类 stage（如多个 reviewer 都是 transcription.reviewers）
        merged: dict[str, float] = {}
        for t in stage_timings:
            key = f"{t.get('stage','?')}[{t.get('model','')}]" if t.get("model") else t.get("stage", "?")
            merged[key] = merged.get(key, 0) + float(t.get("duration_s", 0))
        detail_parts = [f"{k}={_format_duration(v)}" for k, v in merged.items()]
        return header + "\n  " + "，".join(detail_parts)
    return header


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PreprocessConfig(_StrictModel):
    enabled: bool = False
    noise_reduction: bool = False
    noise_strength: float = Field(default=8.0, ge=0, le=48)
    sample_rate: int = Field(default=16000, gt=0, le=192000)
    loudnorm: bool = True
    silence_mode: Literal["preserve_timeline", "trim_silence"] = "preserve_timeline"


class SegmentationConfig(_StrictModel):
    vad_enabled: bool = True
    vad_preset: str = "default"
    # 默认启用音频分块：长音频（如 2h+ 录音）不分块时 ASR 模型一次性处理
    # 会导致大量内容丢失（模型处理超长音频能力有限），分块后每块独立 ASR
    # 可显著提升识别完整度。短音频（< chunk_seconds）分块后仍为 1 块，无副作用。
    chunk_enabled: bool = True
    chunk_seconds: int = Field(default=240, ge=10, le=3600)
    overlap_seconds: int = Field(default=10, ge=0, le=300)


class ModelRunConfig(_StrictModel):
    model: str = "sensevoice"
    weight: float = Field(default=1.0, gt=0, le=10)
    language: str = "auto"
    hotword: str = ""
    use_itn: bool | None = None
    punc_mode: Literal["auto", "disabled"] = "auto"


class TranscriptionConfig(_StrictModel):
    mode: Literal["single_model", "multi_model"] = "single_model"
    primary: ModelRunConfig = Field(default_factory=ModelRunConfig)
    reviewers: list[ModelRunConfig] = Field(default_factory=list)
    execution: Literal["serial", "parallel"] = "parallel"
    max_concurrency: int = Field(default=1, ge=1, le=8)
    resource_failure_policy: Literal[
        "stop_and_ask", "fallback_to_serial", "skip_failed_reviewer"
    ] = "stop_and_ask"


class TimestampConfig(_StrictModel):
    level: Literal["off", "segment", "word"] = "segment"
    forced_alignment: bool = False
    # 默认对齐模型——当用户选择字词级时间戳但未显式填时，后端自动启用
    aligner_model: str = "Qwen/Qwen3-ForcedAligner-0.6B"


def auto_fill_forced_alignment(config: WorkflowConfig) -> None:
    """按时间戳粒度自动推断并补全强制对齐配置。

    规则：
    - 用户选 "word"（字词级）时间戳，但没有显式改 forced_alignment → 自动开启 + 默认模型
    - 用户选 "segment" 或 "off" → forced_alignment=False
    - forced_alignment=True 但 aligner_model 为空 → 用默认模型
    - 用户显式设置了 forced_alignment（不管 True/False）→ 尊重用户选择，不覆盖
    """
    ts = config.timestamps

    # Pydantic 默认值 forced_alignment=False + aligner_model="Qwen/..."
    # 用模型里的 sentinel 不好判断"用户显式传了 False 还是吃的默认"，
    # 简化策略：只在 level="word" 时强制启用；其余情况关闭 forced_alignment 但保留模型路径
    if ts.level == "word":
        ts.forced_alignment = True
        if not ts.aligner_model.strip():
            ts.aligner_model = "Qwen/Qwen3-ForcedAligner-0.6B"
    else:
        ts.forced_alignment = False


def auto_fill_transcription_mode(config: WorkflowConfig) -> None:
    """校对模型非空时自动把转录模式切到 multi_model。

    前端选了 reviewer 但忘了切模式（或 API 直调）时，后端兜底。
    """
    tr = config.transcription
    has_reviewers = bool(tr.reviewers)
    if has_reviewers and tr.mode != "multi_model":
        tr.mode = "multi_model"
    elif not has_reviewers and tr.mode == "multi_model":
        tr.mode = "single_model"


class DiarizationConfig(_StrictModel):
    enabled: bool = False
    strategy: Literal["joint", "separate_align"] = "separate_align"
    # 空字符串 = 优先复用 primary 的 ASR 结果（省掉单独跑 3D-Speaker 的时间）
    # 只有当 primary 没带 diarization 或用户明确指定了别的模型时才单独跑
    asr_model: str = ""
    speaker_model: str = "cam++"
    spk_mode: Literal["default", "vad_segment", "punc_segment"] = "punc_segment"
    preset_speaker_count: int | None = Field(default=None, ge=1, le=100)
    global_speaker_clustering: bool = True


class ReconciliationConfig(_StrictModel):
    mode: Literal["primary_first", "weighted_consensus"] = "primary_first"
    disagreement_threshold: float = Field(default=0.2, ge=0, le=1)
    keep_alternatives: bool = True
    uncertain_policy: Literal["keep_primary", "flag_for_review"] = "flag_for_review"


class LLMStageConfig(_StrictModel):
    enabled: bool = False
    provider_profile_id: str = ""
    model: str = ""
    scope: Literal["all", "segments", "original", "refined"] = "all"
    template_id: Literal["default", "strict", "meeting"] = "default"
    preserve_timestamps: bool = True
    preserve_speakers: bool = True


class TranslationStageConfig(_StrictModel):
    enabled: bool = False
    model: str = "nllb-200-distilled-600m"
    source_lang: str = ""
    target_lang: str = ""


class EmotionStageConfig(_StrictModel):
    enabled: bool = False
    model: str = "emotion2vec-plus-large"
    granularity: Literal["utterance", "frame"] = "utterance"


class ExportConfig(_StrictModel):
    formats: list[Literal["json", "txt", "srt", "vtt", "tsv", "all"]] = Field(
        default_factory=lambda: ["json", "txt", "srt", "vtt", "tsv"]
    )
    include_raw_candidates: bool = False
    include_config_snapshot: bool = True


class WorkflowConfig(_StrictModel):
    workflow_version: str = "1.0"
    preset_id: str = "custom"
    preprocess: PreprocessConfig = Field(default_factory=PreprocessConfig)
    segmentation: SegmentationConfig = Field(default_factory=SegmentationConfig)
    transcription: TranscriptionConfig = Field(default_factory=TranscriptionConfig)
    timestamps: TimestampConfig = Field(default_factory=TimestampConfig)
    diarization: DiarizationConfig = Field(default_factory=DiarizationConfig)
    reconciliation: ReconciliationConfig = Field(default_factory=ReconciliationConfig)
    llm_proofread: LLMStageConfig = Field(default_factory=LLMStageConfig)
    summary: LLMStageConfig = Field(default_factory=LLMStageConfig)
    mindmap: LLMStageConfig = Field(default_factory=LLMStageConfig)
    translation: TranslationStageConfig = Field(default_factory=TranslationStageConfig)
    emotion: EmotionStageConfig = Field(default_factory=EmotionStageConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)


class WorkflowConfigError(ValueError):
    """工作流 JSON 无法解析或不符合 schema。"""


class WorkflowCancelled(RuntimeError):
    """工作流收到取消请求。"""


def parse_workflow_config(raw: dict[str, Any] | WorkflowConfig) -> WorkflowConfig:
    """将字典解析为严格工作流配置。"""
    if isinstance(raw, WorkflowConfig):
        return raw
    try:
        return WorkflowConfig.model_validate(raw)
    except ValidationError as exc:
        raise WorkflowConfigError(str(exc)) from exc


def workflow_config_to_dict(config: WorkflowConfig) -> dict[str, Any]:
    """返回可序列化配置快照。"""
    return config.model_dump(mode="json")


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def validate_workflow_config(
    config: WorkflowConfig,
    model_capabilities: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """校验跨阶段依赖和模型能力，返回 errors 与 warnings。"""
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    transcription = config.transcription
    selected_models = [transcription.primary, *transcription.reviewers]

    if (
        config.segmentation.chunk_enabled
        and config.segmentation.overlap_seconds >= config.segmentation.chunk_seconds
    ):
        errors.append(
            _issue(
                "CHUNK_OVERLAP_INVALID",
                "segmentation.overlap_seconds",
                "块间重叠秒数必须小于每块秒数",
            )
        )

    if transcription.mode == "multi_model" and not transcription.reviewers:
        errors.append(
            _issue(
                "MULTI_MODEL_REVIEWER_REQUIRED",
                "transcription.reviewers",
                "多模型模式至少需要一个校对模型",
            )
        )

    model_names = [item.model for item in selected_models]
    if len(model_names) != len(set(model_names)):
        errors.append(
            _issue(
                "DUPLICATE_TRANSCRIPTION_MODEL",
                "transcription",
                "主模型和校对模型不能重复",
            )
        )

    for index, item in enumerate(selected_models):
        path = "transcription.primary" if index == 0 else f"transcription.reviewers[{index - 1}]"
        capabilities = model_capabilities.get(item.model)
        if capabilities is None:
            errors.append(_issue("MODEL_NOT_FOUND", f"{path}.model", f"未知模型：{item.model}"))
        elif not capabilities.get("offline_asr", False):
            errors.append(
                _issue(
                    "MODEL_CAPABILITY_MISMATCH",
                    f"{path}.model",
                    f"模型 {item.model} 不支持离线转录",
                )
            )
        elif capabilities.get("downloaded") is False:
            errors.append(
                _issue(
                    "MODEL_NOT_DOWNLOADED",
                    f"{path}.model",
                    f"模型 {item.model} 尚未下载；请先执行明确的下载操作",
                )
            )

    if config.diarization.enabled:
        capabilities = model_capabilities.get(config.diarization.asr_model)
        if capabilities is None:
            errors.append(
                _issue(
                    "MODEL_NOT_FOUND",
                    "diarization.asr_model",
                    f"未知说话人辅助 ASR 模型：{config.diarization.asr_model}",
                )
            )
        elif not capabilities.get("diarization", False):
            errors.append(
                _issue(
                    "MODEL_CAPABILITY_MISMATCH",
                    "diarization.asr_model",
                    f"模型 {config.diarization.asr_model} 不支持说话人分离",
                )
            )
        elif capabilities.get("downloaded") is False:
            errors.append(
                _issue(
                    "MODEL_NOT_DOWNLOADED",
                    "diarization.asr_model",
                    f"说话人辅助模型 {config.diarization.asr_model} 尚未下载",
                )
            )
        if config.timestamps.level == "off":
            errors.append(
                _issue(
                    "DIARIZATION_REQUIRES_TIMESTAMPS",
                    "timestamps.level",
                    "说话人对齐需要段级或字词级时间戳",
                )
            )

    if config.timestamps.level == "off" and set(config.export.formats) & {"srt", "vtt", "tsv"}:
        errors.append(
            _issue(
                "SUBTITLE_REQUIRES_TIMESTAMPS",
                "export.formats",
                "SRT、VTT 和 TSV 导出需要时间戳",
            )
        )

    if config.timestamps.forced_alignment and not config.timestamps.aligner_model:
        errors.append(
            _issue(
                "ALIGNER_MODEL_REQUIRED",
                "timestamps.aligner_model",
                "启用强制对齐后必须选择对齐模型",
            )
        )
    if config.timestamps.forced_alignment:
        primary_capabilities = model_capabilities.get(transcription.primary.model, {})
        if not primary_capabilities.get("forced_alignment", False):
            errors.append(
                _issue(
                    "FORCED_ALIGNMENT_UNSUPPORTED_PRIMARY",
                    "transcription.primary.model",
                    "当前主模型不支持所选强制对齐流程，请选择支持强制对齐的 ASR 模型",
                )
            )

    if config.timestamps.level == "word" and not config.timestamps.forced_alignment:
        errors.append(
            _issue(
                "WORD_TIMESTAMPS_REQUIRE_ALIGNMENT",
                "timestamps.forced_alignment",
                "字词级时间戳必须启用支持模型的强制对齐",
            )
        )

    if config.diarization.enabled and config.diarization.strategy != "separate_align":
        errors.append(
            _issue(
                "DIARIZATION_STRATEGY_UNSUPPORTED",
                "diarization.strategy",
                "统一工作流当前只支持独立说话人识别后按时间轴对齐",
            )
        )
    if config.diarization.enabled and not config.diarization.global_speaker_clustering:
        errors.append(
            _issue(
                "GLOBAL_SPEAKER_CLUSTERING_REQUIRED",
                "diarization.global_speaker_clustering",
                "当前工作流对整段音频执行全局聚类，不能关闭该选项",
            )
        )

    if transcription.execution == "parallel" and transcription.max_concurrency > 1:
        warnings.append(
            _issue(
                "PARALLEL_RESOURCE_RISK",
                "transcription.max_concurrency",
                "并行加载多个 ASR 模型可能造成显存不足，请确认资源状态",
            )
        )

    for stage_name in ("llm_proofread", "summary", "mindmap"):
        stage = getattr(config, stage_name)
        if stage.enabled and (not stage.provider_profile_id or not stage.model):
            errors.append(
                _issue(
                    "LLM_MODEL_REQUIRED",
                    stage_name,
                    f"启用 {stage_name} 后必须选择 provider profile 和模型",
                )
            )
    if (
        config.llm_proofread.enabled
        and config.llm_proofread.scope == "all"
        and config.llm_proofread.preserve_timestamps
        and config.timestamps.level != "off"
    ):
        errors.append(
            _issue(
                "LLM_SCOPE_CANNOT_PRESERVE_TIMESTAMPS",
                "llm_proofread.scope",
                "整篇校对无法可靠映射回时间段；请选择逐段校对或关闭时间戳保留",
            )
        )

    if config.translation.enabled:
        capabilities = model_capabilities.get(config.translation.model)
        if capabilities is None:
            errors.append(
                _issue(
                    "MODEL_NOT_FOUND",
                    "translation.model",
                    f"未知翻译模型：{config.translation.model}",
                )
            )
        elif not capabilities.get("translation", False):
            errors.append(
                _issue(
                    "MODEL_CAPABILITY_MISMATCH",
                    "translation.model",
                    f"模型 {config.translation.model} 不支持翻译",
                )
            )
        elif capabilities.get("downloaded") is False:
            errors.append(
                _issue(
                    "MODEL_NOT_DOWNLOADED",
                    "translation.model",
                    f"翻译模型 {config.translation.model} 尚未下载",
                )
            )
        if not config.translation.source_lang or not config.translation.target_lang:
            errors.append(
                _issue(
                    "TRANSLATION_LANGUAGES_REQUIRED",
                    "translation",
                    "启用翻译后必须选择源语言和目标语言",
                )
            )
        elif config.translation.source_lang == config.translation.target_lang:
            errors.append(
                _issue(
                    "TRANSLATION_LANGUAGES_IDENTICAL",
                    "translation.target_lang",
                    "源语言和目标语言不能相同",
                )
            )

    if config.emotion.enabled:
        capabilities = model_capabilities.get(config.emotion.model)
        if capabilities is None:
            errors.append(
                _issue(
                    "MODEL_NOT_FOUND",
                    "emotion.model",
                    f"未知情感模型：{config.emotion.model}",
                )
            )
        elif not capabilities.get("emotion", False):
            errors.append(
                _issue(
                    "MODEL_CAPABILITY_MISMATCH",
                    "emotion.model",
                    f"模型 {config.emotion.model} 不支持情感识别",
                )
            )
        elif capabilities.get("downloaded") is False:
            errors.append(
                _issue(
                    "MODEL_NOT_DOWNLOADED",
                    "emotion.model",
                    f"情感模型 {config.emotion.model} 尚未下载",
                )
            )
        if config.emotion.model == "sensevoice" and config.emotion.granularity != "utterance":
            errors.append(
                _issue(
                    "EMOTION_GRANULARITY_UNSUPPORTED",
                    "emotion.granularity",
                    "SenseVoice 情感识别只支持 utterance 粒度",
                )
            )

    return errors, warnings


class WorkflowRunContext:
    """提供给具体工作流执行器的状态上报和取消接口。"""

    def __init__(self, manager: "WorkflowJobManager", job_id: str):
        self._manager = manager
        self.job_id = job_id

    @property
    def config(self) -> dict[str, Any]:
        return self._manager._get_private(self.job_id)["config"]

    @property
    def source_path(self) -> str:
        return self._manager._get_private(self.job_id)["source_path"]

    @property
    def cancelled(self) -> bool:
        return self._manager._get_private(self.job_id)["cancel_event"].is_set()

    @property
    def events(self) -> list[dict[str, Any]]:
        """返回当前任务事件快照，供产物导出保留审计记录。"""
        return copy.deepcopy(self._manager._get_private(self.job_id)["events"])

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise WorkflowCancelled("任务已取消")

    def emit(self, **event: Any) -> dict[str, Any]:
        return self._manager._emit(self.job_id, **event)


class WorkflowJobManager:
    """进程内工作流任务队列。"""

    TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

    def __init__(
        self,
        *,
        max_workers: int = 1,
        terminal_callback: Callable[[dict[str, Any]], None] | None = None,
        terminal_ttl_s: int = 24 * 3600,
        max_terminal_jobs: int = 100,
    ):
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._terminal_callback = terminal_callback
        self._terminal_ttl_s = max(60, int(terminal_ttl_s))
        self._max_terminal_jobs = max(1, int(max_terminal_jobs))
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="pat-workflow",
        )

    def submit(
        self,
        *,
        config: dict[str, Any],
        source_path: str,
        runner: Callable[[WorkflowRunContext], dict[str, Any] | None],
    ) -> str:
        job_id = f"wf_{uuid.uuid4().hex}"
        trace_id = f"trace_{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "trace_id": trace_id,
                "status": "queued",
                "progress": 0.0,
                "current_stage": "queue",
                "current_model": "",
                "created_at": now,
                "updated_at": now,
                "config": copy.deepcopy(config),
                "source_path": str(source_path),
                "events": [],
                "result": None,
                "error": None,
                "cancel_event": threading.Event(),
                "next_event_id": 1,
                "future": None,
                "stage_timings": [],       # [{stage, model, start, end, duration_s}]
                "_active_timing": None,    # 当前正在计时的 {stage, model, start_dt}
            }
            self._emit_locked(
                job_id,
                level="info",
                stage="queue",
                stage_status="pending",
                progress=0.0,
                message="任务已进入队列",
            )
            future = self._executor.submit(self._execute, job_id, runner)
            self._jobs[job_id]["future"] = future
        return job_id

    def _execute(
        self,
        job_id: str,
        runner: Callable[[WorkflowRunContext], dict[str, Any] | None],
    ) -> None:
        context = WorkflowRunContext(self, job_id)
        try:
            context.raise_if_cancelled()
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = "running"
            context.emit(
                level="progress",
                stage="workflow",
                stage_status="running",
                progress=0.0,
                message="任务开始执行",
            )
            result = runner(context)
            context.raise_if_cancelled()
            with self._lock:
                job = self._jobs[job_id]
                job["result"] = result or {}
            # 汇总事件：开始时间 / 结束时间 / 总耗时 + 分阶段耗时
            now_iso = datetime.now(timezone.utc).isoformat()
            with self._lock:
                timings = self._finalize_stage_timing(job_id, now_iso)
                # 把 stage_timings 保存到 job 供 snapshot 返回
                job["stage_timings"] = timings
            summary = _build_summary_message(
                job["created_at"], now_iso, status_label="完成", stage_timings=timings
            )
            context.emit(level="info", stage="summary", stage_status="success", progress=None, message=summary)
            context.emit(
                level="success",
                stage="workflow",
                stage_status="success",
                progress=1.0,
                message="任务完成",
            )
            self._run_terminal_callback(job_id)
            with self._lock:
                self._jobs[job_id]["status"] = "completed"
        except WorkflowCancelled as exc:
            safe_message = _safe_error_message(exc)
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = "cancelled"
                job["error"] = safe_message
            now_iso = datetime.now(timezone.utc).isoformat()
            with self._lock:
                timings = self._finalize_stage_timing(job_id, now_iso)
                job["stage_timings"] = timings
            summary = _build_summary_message(
                job["created_at"], now_iso, status_label="取消", stage_timings=timings
            )
            context.emit(level="info", stage="summary", stage_status="cancelled", progress=None, message=summary)
            context.emit(
                level="warning",
                stage="workflow",
                stage_status="cancelled",
                progress=None,
                message=safe_message,
                error_code="WORKFLOW_CANCELLED",
                retryable=False,
            )
        except Exception as exc:
            safe_message = _safe_error_message(exc)
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = "failed"
                job["error"] = safe_message
            now_iso = datetime.now(timezone.utc).isoformat()
            with self._lock:
                timings = self._finalize_stage_timing(job_id, now_iso)
                job["stage_timings"] = timings
            summary = _build_summary_message(
                job["created_at"], now_iso, status_label="失败", stage_timings=timings
            )
            context.emit(level="info", stage="summary", stage_status="error", progress=None, message=summary)
            context.emit(
                level="error",
                stage="workflow",
                stage_status="error",
                progress=None,
                message=safe_message,
                error_code="WORKFLOW_FAILED",
                retryable=False,
            )
            self._run_terminal_callback(job_id)

    def _run_terminal_callback(self, job_id: str) -> None:
        if self._terminal_callback is None:
            return
        try:
            self._terminal_callback(self.get_snapshot(job_id, include_internal=True))
        except Exception:
            # 终态回调只负责补充审计产物，不能反向改变已完成任务状态。
            return

    def _get_private(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            return self._jobs[job_id]

    def _emit(self, job_id: str, **event: Any) -> dict[str, Any]:
        with self._lock:
            return self._emit_locked(job_id, **event)

    def _emit_locked(
        self,
        job_id: str,
        *,
        level: str,
        stage: str,
        message: str,
        progress: float | None = None,
        stage_status: str | None = None,
        model: str = "",
        current: int | None = None,
        total: int | None = None,
        error_code: str = "",
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job = self._jobs[job_id]
        # ---- 自动 stage 计时 ----
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        stage_key = str(stage)
        model_key = str(model or "")
        active = job.get("_active_timing")
        if active is None:
            # 首次 emit，开启计时
            job["_active_timing"] = {"stage": stage_key, "model": model_key, "start_dt": now_dt}
        elif active["stage"] != stage_key or active["model"] != model_key:
            # stage/model 切换，关掉上一个
            duration = (now_dt - active["start_dt"]).total_seconds()
            job["stage_timings"].append({
                "stage": active["stage"],
                "model": active["model"],
                "start": active["start_dt"].isoformat(),
                "end": now_iso,
                "duration_s": round(duration, 3),
            })
            job["_active_timing"] = {"stage": stage_key, "model": model_key, "start_dt": now_dt}
        # ---- 计时结束 ----
        if progress is not None:
            progress = max(float(job.get("progress", 0.0)), min(1.0, float(progress)))
            job["progress"] = progress
        status_map = {
            "info": "running",
            "progress": "running",
            "success": "success",
            "warning": "warning",
            "error": "error",
        }
        item = {
            "event_id": job["next_event_id"],
            "job_id": job_id,
            "timestamp": now_iso,
            "level": str(level),
            "stage": stage_key,
            "stage_status": stage_status or status_map.get(str(level), "running"),
            "progress": progress,
            "model": model_key,
            "current": current,
            "total": total,
            "message": str(message),
            "error_code": str(error_code or ""),
            "retryable": bool(retryable),
            "trace_id": job["trace_id"],
            "details": copy.deepcopy(details or {}),
        }
        job["next_event_id"] += 1
        job["events"].append(item)
        job["updated_at"] = now_iso
        job["current_stage"] = stage_key
        job["current_model"] = model_key
        return copy.deepcopy(item)

    def _finalize_stage_timing(self, job_id: str, end_iso: str) -> list[dict[str, Any]]:
        """关最后一个计时段，返回完整 stage_timings 列表。"""
        job = self._jobs[job_id]
        active = job.pop("_active_timing", None)
        if active is not None:
            try:
                end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
            except Exception:
                end_dt = datetime.now(timezone.utc)
            duration = (end_dt - active["start_dt"]).total_seconds()
            job["stage_timings"].append({
                "stage": active["stage"],
                "model": active["model"],
                "start": active["start_dt"].isoformat(),
                "end": end_iso,
                "duration_s": round(duration, 3),
            })
        return job["stage_timings"]

    def get_snapshot(
        self,
        job_id: str,
        *,
        include_internal: bool = False,
        include_events: bool = True,
    ) -> dict[str, Any]:
        """返回任务快照；公开快照移除服务端文件系统路径。"""
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            job = self._jobs[job_id]
            snapshot = {
                key: copy.deepcopy(value)
                for key, value in job.items()
                if key not in {"cancel_event", "future", "next_event_id", "source_path", "_active_timing"}
            }
        if not include_internal:
            for artifact in (snapshot.get("result") or {}).get("artifacts") or []:
                if isinstance(artifact, dict):
                    artifact.pop("path", None)
        if not include_events:
            snapshot.pop("events", None)
        return snapshot

    def list_snapshots(self) -> list[dict[str, Any]]:
        with self._lock:
            job_ids = list(self._jobs.keys())
        return [
            self.get_snapshot(job_id, include_events=False)
            for job_id in reversed(job_ids)
        ]

    def get_events(self, job_id: str, *, after_event_id: int = 0) -> dict[str, Any]:
        """只复制调用方需要的增量事件，避免轮询时反复复制完整历史。"""
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            job = self._jobs[job_id]
            events = [
                copy.deepcopy(event)
                for event in job["events"]
                if int(event.get("event_id", 0)) > int(after_event_id)
            ]
            return {"status": job["status"], "events": events}

    def prune_terminal_jobs(self, *, now: float | None = None) -> int:
        """按 TTL 和数量上限清理终态任务的进程内快照。"""
        current_time = float(time.time() if now is None else now)
        with self._lock:
            terminal = [
                (job_id, job)
                for job_id, job in self._jobs.items()
                if job.get("status") in self.TERMINAL_STATUSES
            ]
            terminal.sort(key=lambda item: str(item[1].get("updated_at") or ""))
            removable: set[str] = set()
            for job_id, job in terminal:
                try:
                    updated = datetime.fromisoformat(str(job.get("updated_at"))).timestamp()
                except Exception:
                    updated = current_time
                if current_time - updated > self._terminal_ttl_s:
                    removable.add(job_id)
            remaining = [item for item in terminal if item[0] not in removable]
            overflow = max(0, len(remaining) - self._max_terminal_jobs)
            removable.update(job_id for job_id, _job in remaining[:overflow])
            for job_id in removable:
                self._jobs.pop(job_id, None)
            return len(removable)

    def queue_summary(self) -> dict[str, Any]:
        """返回任务队列状态计数，供运行面板和健康检查复用。"""
        counts = {
            "queued": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
        }
        with self._lock:
            for job in self._jobs.values():
                status = str(job.get("status") or "queued")
                counts[status] = counts.get(status, 0) + 1
        return {
            "total": sum(counts.values()),
            "active": counts["queued"] + counts["running"],
            "status_counts": counts,
        }

    def active_source_paths(self) -> list[str]:
        """返回排队或运行任务的输入路径，用于临时目录清理时排除活动任务。"""
        with self._lock:
            return [
                str(job["source_path"])
                for job in self._jobs.values()
                if job.get("status") in {"queued", "running"}
            ]

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            job = self._jobs[job_id]
            if job["status"] in self.TERMINAL_STATUSES:
                return self.get_snapshot(job_id)
            job["cancel_event"].set()
            self._emit_locked(
                job_id,
                level="warning",
                stage="workflow",
                stage_status="cancel_requested",
                progress=None,
                message="已请求取消任务",
                error_code="WORKFLOW_CANCEL_REQUESTED",
                retryable=False,
            )
        return self.get_snapshot(job_id)

    def wait_for_terminal(self, job_id: str, *, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            snapshot = self.get_snapshot(job_id)
            if snapshot["status"] in self.TERMINAL_STATUSES:
                return snapshot
            time.sleep(0.01)
        raise TimeoutError(f"Workflow job did not finish within {timeout} seconds: {job_id}")

    def shutdown(self, *, wait: bool) -> None:
        self._executor.shutdown(wait=bool(wait), cancel_futures=False)
