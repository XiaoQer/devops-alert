from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.domain.evidence import (
    EvidenceContext,
    EvidenceItem,
    EvidenceItemState,
    EvidenceSourceType,
    EvidenceType,
)
from incident_intelligence.domain.models import UtcAwareDatetime

FORBIDDEN_CONCLUSION_WORDS = ("根因", "置信度", "自动修复", "确定导致")


class EvidenceItemDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_key: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1, max_length=160)
    source_type: EvidenceSourceType
    state: EvidenceItemState
    evidence_type: EvidenceType
    query_started_at: UtcAwareDatetime
    query_ended_at: UtcAwareDatetime
    normalized_result: dict[str, object] = Field(default_factory=dict)
    interpretation: str
    error_code: str | None = None


class EvidenceCorrelationService:
    def correlate(
        self,
        context: EvidenceContext,
        items: tuple[EvidenceItem, ...],
    ) -> tuple[EvidenceItemDraft, ...]:
        if context.service_name is None:
            return ()
        trace_items = tuple(item for item in items if item.evidence_type == "TRACE_SUMMARY")
        log_items = tuple(
            item for item in items if item.evidence_type in {"LOG_SAMPLE", "LOG_AGGREGATION"}
        )
        if not trace_items or not log_items:
            return ()
        query_started_at = min(item.query_started_at for item in (*trace_items, *log_items))
        query_ended_at = max(item.query_ended_at for item in (*trace_items, *log_items))
        trace_failed = any(
            item.state in {"FAILED", "MISSING_TARGET", "SKIPPED_DEPENDENCY"} for item in trace_items
        )
        log_available = any(item.state in {"SUCCEEDED", "NO_DATA"} for item in log_items)
        if trace_failed and log_available:
            return (
                _draft(
                    state="SKIPPED_DEPENDENCY",
                    query_started_at=query_started_at,
                    query_ended_at=query_ended_at,
                    normalized_result={"missing_source": "SKYWALKING"},
                    interpretation="链路证据不可用。未执行 Trace 与日志匹配。",
                    error_code="skywalking_dependency_unavailable",
                ),
            )

        trace_ids = _trace_ids(trace_items)
        log_trace_ids = _log_trace_ids(log_items)
        matched = tuple(sorted(trace_ids.intersection(log_trace_ids)))
        if not matched or not _has_overlapping_window(trace_items, log_items):
            return ()
        return (
            _draft(
                state="SUCCEEDED",
                query_started_at=query_started_at,
                query_ended_at=query_ended_at,
                normalized_result={
                    "matched_trace_count": len(matched),
                    "trace_ids": list(matched[:20]),
                    "environment": context.environment,
                    "service": context.service_name,
                },
                interpretation="失败 Trace 在同服务、同时间窗口找到对应日志",
            ),
        )


def _draft(
    *,
    state: EvidenceItemState,
    query_started_at: UtcAwareDatetime,
    query_ended_at: UtcAwareDatetime,
    normalized_result: dict[str, object],
    interpretation: str,
    error_code: str | None = None,
) -> EvidenceItemDraft:
    if any(word in interpretation for word in FORBIDDEN_CONCLUSION_WORDS):
        raise ValueError("forbidden_correlation_conclusion")
    return EvidenceItemDraft(
        evidence_key="cross-source.trace-log-match",
        display_name="Trace 与日志关联",
        source_type="PLATFORM",
        state=state,
        evidence_type="CROSS_SOURCE_CORRELATION",
        query_started_at=query_started_at,
        query_ended_at=query_ended_at,
        normalized_result=normalized_result,
        interpretation=interpretation,
        error_code=error_code,
    )


def _trace_ids(items: tuple[EvidenceItem, ...]) -> set[str]:
    result: set[str] = set()
    for item in items:
        traces = item.normalized_result.get("traces")
        if not isinstance(traces, list):
            continue
        for trace in traces:
            if isinstance(trace, dict):
                trace_id = trace.get("trace_id")
                if isinstance(trace_id, str) and trace_id:
                    result.add(trace_id[:128])
    return result


def _log_trace_ids(items: tuple[EvidenceItem, ...]) -> set[str]:
    result: set[str] = set()
    for item in items:
        samples = item.normalized_result.get("samples")
        if not isinstance(samples, list):
            continue
        for sample in samples:
            if isinstance(sample, dict):
                trace_id = sample.get("trace_id")
                if isinstance(trace_id, str) and trace_id:
                    result.add(trace_id[:128])
    return result


def _has_overlapping_window(
    left_items: tuple[EvidenceItem, ...],
    right_items: tuple[EvidenceItem, ...],
) -> bool:
    return any(
        left.query_started_at < right.query_ended_at
        and right.query_started_at < left.query_ended_at
        for left in left_items
        for right in right_items
    )
