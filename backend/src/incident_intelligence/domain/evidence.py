from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from incident_intelligence.domain.models import (
    AlertName,
    Environment,
    FactKey,
    FactValue,
    ServiceName,
    UtcAwareDatetime,
)

EvidenceRunTrigger = Literal["AUTOMATIC", "MANUAL"]
EvidenceRunState = Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED"]
EvidenceItemState = Literal[
    "SUCCEEDED",
    "NO_DATA",
    "INSUFFICIENT_BASELINE",
    "MISSING_TARGET",
    "SKIPPED_DEPENDENCY",
    "FAILED",
]
MonitoringSourceType = Literal["PROMETHEUS", "ELASTICSEARCH", "SKYWALKING"]
EvidenceSourceType = Literal[
    "PROMETHEUS",
    "ELASTICSEARCH",
    "SKYWALKING",
    "PLATFORM",
]
EvidenceType = Literal[
    "METRIC_TIMESERIES",
    "METRIC_COMPARISON",
    "LOG_AGGREGATION",
    "LOG_SAMPLE",
    "ENDPOINT_RANKING",
    "DEPENDENCY_RANKING",
    "TRACE_SUMMARY",
    "CROSS_SOURCE_CORRELATION",
]

EvidenceKey = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=160),
]
EvidenceDisplayName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=160),
]
_UTC_DATETIME_ADAPTER = TypeAdapter(UtcAwareDatetime)
EvidenceIdentifier = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9._-]{0,127}$"),
]


class _FrozenEvidenceModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvidenceWindow(_FrozenEvidenceModel):
    baseline_start: UtcAwareDatetime
    baseline_end: UtcAwareDatetime
    fault_start: UtcAwareDatetime
    fault_end: UtcAwareDatetime

    @model_validator(mode="after")
    def validate_order(self) -> EvidenceWindow:
        if not (self.baseline_start < self.baseline_end <= self.fault_start <= self.fault_end):
            raise ValueError("evidence_window_invalid")
        if self.fault_end - self.baseline_start > timedelta(hours=2):
            raise ValueError("evidence_window_exceeds_two_hours")
        return self


class EvidenceContext(_FrozenEvidenceModel):
    environment: Environment
    service_name: ServiceName | None
    alert_names: tuple[AlertName, ...] = Field(max_length=200)
    facts: dict[FactKey, FactValue] = Field(default_factory=dict, max_length=50)

    @model_validator(mode="after")
    def reject_fault_injection_identity(self) -> EvidenceContext:
        forbidden = {"scenario_id", "scenario_version", "experiment_id"}
        if forbidden.intersection(self.facts):
            raise ValueError("forbidden_fault_injection_identity")
        return self


class EvidenceRun(_FrozenEvidenceModel):
    id: str = Field(pattern=r"^evr_[0-9a-f]{32}$")
    incident_id: str = Field(pattern=r"^inc_[0-9a-f]{32}$")
    trigger: EvidenceRunTrigger
    state: EvidenceRunState
    anchor_at: UtcAwareDatetime
    window: EvidenceWindow
    context: EvidenceContext
    package_versions: dict[EvidenceIdentifier, int] = Field(
        default_factory=dict,
        max_length=5,
    )
    succeeded_count: int = Field(default=0, ge=0, le=1_000)
    skipped_count: int = Field(default=0, ge=0, le=1_000)
    missing_count: int = Field(default=0, ge=0, le=1_000)
    failed_count: int = Field(default=0, ge=0, le=1_000)
    failure_summary: str | None = Field(default=None, max_length=1_000)
    requested_by: str = Field(min_length=1, max_length=128)
    created_at: UtcAwareDatetime
    started_at: UtcAwareDatetime | None = None
    completed_at: UtcAwareDatetime | None = None
    version: int = Field(default=1, ge=1)


class EvidenceItem(_FrozenEvidenceModel):
    id: str = Field(pattern=r"^evitem_[0-9a-f]{32}$")
    evidence_run_id: str = Field(pattern=r"^evr_[0-9a-f]{32}$")
    evidence_key: EvidenceKey
    display_name: EvidenceDisplayName
    source_type: EvidenceSourceType
    state: EvidenceItemState
    package_id: EvidenceIdentifier
    package_version: int = Field(ge=1)
    template_id: EvidenceIdentifier
    template_version: int = Field(ge=1)
    evidence_type: EvidenceType
    query_started_at: UtcAwareDatetime
    query_ended_at: UtcAwareDatetime
    step_seconds: int | None = Field(default=None, ge=1, le=3_600)
    query_parameters: dict[str, str | int | float | bool] = Field(
        default_factory=dict,
        max_length=20,
    )
    baseline_summary: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict,
        max_length=50,
    )
    fault_summary: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict,
        max_length=50,
    )
    interpretation: str | None = Field(default=None, max_length=2_000)
    normalized_result: dict[str, object] = Field(default_factory=dict)
    error_code: str | None = Field(default=None, max_length=64)
    created_at: UtcAwareDatetime

    @model_validator(mode="after")
    def validate_query_window(self) -> EvidenceItem:
        if self.query_started_at > self.query_ended_at:
            raise ValueError("evidence_item_query_window_invalid")
        return self


def build_evidence_window(
    anchor_at: datetime,
    run_started_at: datetime,
) -> EvidenceWindow:
    anchor_at = _UTC_DATETIME_ADAPTER.validate_python(anchor_at)
    run_started_at = _UTC_DATETIME_ADAPTER.validate_python(run_started_at)
    capped_end = min(run_started_at, anchor_at + timedelta(minutes=90))
    return EvidenceWindow(
        baseline_start=anchor_at - timedelta(minutes=30),
        baseline_end=anchor_at - timedelta(minutes=10),
        fault_start=anchor_at - timedelta(minutes=10),
        fault_end=capped_end,
    )


def summarize_run_status(
    item_states: tuple[EvidenceItemState, ...],
) -> Literal["SUCCEEDED", "PARTIAL", "FAILED"]:
    successful_states = {"SUCCEEDED", "NO_DATA"}
    has_success = any(state in successful_states for state in item_states)
    has_incomplete = any(state not in successful_states for state in item_states)
    if has_success and has_incomplete:
        return "PARTIAL"
    if has_success:
        return "SUCCEEDED"
    return "FAILED"
