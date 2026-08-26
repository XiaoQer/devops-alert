from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.alert_sources import AlertSourceType
from incident_intelligence.domain.models import Environment, Severity
from incident_intelligence.persistence.alert_center_repository import (
    AlertCenterRepository,
    CorrelationRecord,
)
from incident_intelligence.persistence.models import AlertSourceRow, SignalEventRow

AlertStateValue = Literal["ACTIVE", "RESOLVED", "SUPPRESSED"]
SummaryWindow = Literal["1h", "24h", "7d"]
CorrelationStatus = Literal["WAITING", "PROCESSING", "FAILED", "COMPLETED"]


class AlertListFilters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    alert_source_id: str | None = Field(default=None, pattern=r"^src_[0-9a-f]{32}$")
    state: AlertStateValue | None = None
    severity: Severity | None = None
    service: str | None = Field(default=None, min_length=1, max_length=128)
    environment: Environment | None = None
    incident_linked: bool | None = None
    observed_from: datetime | None = None
    observed_to: datetime | None = None
    query: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10_000)

    @model_validator(mode="after")
    def validate_time_range(self) -> AlertListFilters:
        if (
            self.observed_from is not None
            and self.observed_to is not None
            and self.observed_from > self.observed_to
        ):
            raise ValueError("开始时间不得晚于结束时间")
        return self


class AlertSourceBrief(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    source_type: AlertSourceType
    management_type: str


class AlertListItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str
    state: AlertStateValue
    severity: Severity
    service: str
    environment: Environment
    source: AlertSourceBrief
    first_observed_at: datetime
    last_observed_at: datetime
    state_changed_at: datetime
    signal_count: int = Field(ge=1)
    incident_id: str | None
    version: int = Field(ge=1)


class AlertPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[AlertListItem, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=10_000)


class AlertSourceSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: AlertSourceBrief
    active: int = Field(ge=0)


class AlertSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    window: SummaryWindow
    active: int = Field(ge=0)
    severe_active: int = Field(ge=0)
    resolved: int = Field(ge=0)
    unlinked_active: int = Field(ge=0)
    by_source: tuple[AlertSourceSummary, ...] = Field(max_length=100)
    calculated_at: datetime


class AlertDetectionView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    summary: str
    observed_at: datetime
    received_at: datetime
    facts: dict[str, str] = Field(max_length=50)


class AlertSignalView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    event_type: str
    summary: str
    severity: Severity
    observed_at: datetime
    received_at: datetime
    facts: dict[str, str] = Field(max_length=50)


class AlertIncidentBrief(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str
    state: str
    severity: str


class AlertCorrelationView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: CorrelationStatus
    job_state: str | None
    outcome: str | None
    explanation: str
    reason_codes: tuple[str, ...] = Field(max_length=10)
    incident: AlertIncidentBrief | None


class AlertProcessingStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    detail: str
    status: Literal["completed", "processing", "failed", "waiting"]


class AlertOverview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str
    state: AlertStateValue
    severity: Severity
    service: str
    environment: Environment
    source: AlertSourceBrief
    first_observed_at: datetime
    last_observed_at: datetime
    state_changed_at: datetime
    version: int
    detection: AlertDetectionView
    signals: tuple[AlertSignalView, ...] = Field(max_length=100)
    signals_truncated: bool
    correlation: AlertCorrelationView
    processing_steps: tuple[AlertProcessingStep, ...] = Field(min_length=3, max_length=3)


class AlertResourceNotFound(Exception):
    pass


class AlertCenterService:
    def __init__(self, *, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_alerts(self, filters: AlertListFilters) -> AlertPage:
        query = filters.query.strip() if filters.query is not None else None
        if query == "":
            query = None
        with self._session_factory() as session:
            repository = AlertCenterRepository(session)
            rows = repository.list_alerts(
                alert_source_id=filters.alert_source_id,
                state=filters.state,
                severity=filters.severity,
                service=filters.service,
                environment=filters.environment,
                incident_linked=filters.incident_linked,
                observed_from=_utc(filters.observed_from),
                observed_to=_utc(filters.observed_to),
                query=query,
                limit=filters.limit,
                offset=filters.offset,
            )
            total = repository.count_alerts(
                alert_source_id=filters.alert_source_id,
                state=filters.state,
                severity=filters.severity,
                service=filters.service,
                environment=filters.environment,
                incident_linked=filters.incident_linked,
                observed_from=_utc(filters.observed_from),
                observed_to=_utc(filters.observed_to),
                query=query,
            )
            return AlertPage(
                items=tuple(
                    AlertListItem(
                        id=row.alert.id,
                        title=row.alert.title,
                        state=cast(AlertStateValue, row.alert.state),
                        severity=cast(Severity, row.alert.severity),
                        service=row.alert.service,
                        environment=cast(Environment, row.alert.environment),
                        source=_source_brief(row.source),
                        first_observed_at=row.alert.first_observed_at,
                        last_observed_at=row.alert.last_observed_at,
                        state_changed_at=row.alert.state_changed_at,
                        signal_count=row.signal_count,
                        incident_id=row.incident_id,
                        version=row.alert.version,
                    )
                    for row in rows
                ),
                total=total,
                limit=filters.limit,
                offset=filters.offset,
            )

    def summarize(self, window: SummaryWindow, *, now: datetime) -> AlertSummary:
        calculated_at = now.astimezone(UTC)
        cutoff = (
            calculated_at
            - {
                "1h": timedelta(hours=1),
                "24h": timedelta(hours=24),
                "7d": timedelta(days=7),
            }[window]
        )
        with self._session_factory() as session:
            repository = AlertCenterRepository(session)
            active, severe_active, resolved, unlinked_active = repository.summary_counts(
                cutoff=cutoff
            )
            return AlertSummary(
                window=window,
                active=active,
                severe_active=severe_active,
                resolved=resolved,
                unlinked_active=unlinked_active,
                by_source=tuple(
                    AlertSourceSummary(source=_source_brief(source), active=count)
                    for source, count in repository.active_by_source()
                ),
                calculated_at=calculated_at,
            )

    def get_overview(self, alert_id: str) -> AlertOverview:
        with self._session_factory() as session:
            repository = AlertCenterRepository(session)
            record = repository.find_alert(alert_id)
            if record is None:
                raise AlertResourceNotFound()
            detection = repository.find_signal(record.alert.signal_event_id)
            if detection is None:
                raise RuntimeError("告警引用的检测信号不存在")
            all_signals = repository.alert_signals(record.alert, limit=101)
            visible_signals = all_signals[:100]
            correlation = _correlation_view(repository.correlation(record.alert.id))
            source = _source_brief(record.source)
            signal_count = len(all_signals)
            return AlertOverview(
                id=record.alert.id,
                title=record.alert.title,
                state=cast(AlertStateValue, record.alert.state),
                severity=cast(Severity, record.alert.severity),
                service=record.alert.service,
                environment=cast(Environment, record.alert.environment),
                source=source,
                first_observed_at=record.alert.first_observed_at,
                last_observed_at=record.alert.last_observed_at,
                state_changed_at=record.alert.state_changed_at,
                version=record.alert.version,
                detection=_detection_view(detection),
                signals=tuple(_signal_view(signal) for signal in visible_signals),
                signals_truncated=len(all_signals) > 100,
                correlation=correlation,
                processing_steps=_processing_steps(source, signal_count, correlation),
            )


def _source_brief(row: AlertSourceRow) -> AlertSourceBrief:
    return AlertSourceBrief(
        id=row.id,
        name=row.name,
        source_type=cast(AlertSourceType, row.source_type),
        management_type=row.management_type,
    )


def _detection_view(row: SignalEventRow) -> AlertDetectionView:
    return AlertDetectionView(
        title=row.title,
        summary=row.summary,
        observed_at=row.observed_at,
        received_at=row.received_at,
        facts=row.facts,
    )


def _signal_view(row: SignalEventRow) -> AlertSignalView:
    return AlertSignalView(
        id=row.id,
        event_type=row.event_type,
        summary=row.summary,
        severity=cast(Severity, row.severity),
        observed_at=row.observed_at,
        received_at=row.received_at,
        facts=row.facts,
    )


def _correlation_view(record: CorrelationRecord) -> AlertCorrelationView:
    if record.job is None:
        return AlertCorrelationView(
            status="WAITING",
            job_state=None,
            outcome=None,
            explanation="告警尚未进入事故关联处理。",
            reason_codes=(),
            incident=None,
        )
    if record.job.state in {"PENDING", "LEASED"}:
        return AlertCorrelationView(
            status="PROCESSING",
            job_state=record.job.state,
            outcome=None,
            explanation="系统正在判断该告警是否应进入已有事故或创建新事故。",
            reason_codes=(),
            incident=None,
        )
    if record.job.state == "FAILED":
        return AlertCorrelationView(
            status="FAILED",
            job_state=record.job.state,
            outcome=None,
            explanation="事故关联暂时失败。告警仍可查看并可等待重新处理。",
            reason_codes=(),
            incident=None,
        )
    decision = record.decision
    return AlertCorrelationView(
        status="COMPLETED",
        job_state=record.job.state,
        outcome=None if decision is None else decision.outcome,
        explanation=(
            "事故关联已完成。没有可展示的规则结论。" if decision is None else decision.explanation
        ),
        reason_codes=() if decision is None else tuple(decision.reason_codes),
        incident=(
            None
            if record.incident is None
            else AlertIncidentBrief(
                id=record.incident.id,
                title=record.incident.title,
                state=record.incident.state,
                severity=record.incident.severity,
            )
        ),
    )


def _processing_steps(
    source: AlertSourceBrief,
    signal_count: int,
    correlation: AlertCorrelationView,
) -> tuple[AlertProcessingStep, ...]:
    aggregation_title = "重复信号已归并" if signal_count > 1 else "首个信号已形成告警"
    aggregation_detail = (
        f"系统已将 {signal_count} 条同一来源的信号归入当前告警。"
        if signal_count > 1
        else "系统根据首个有效信号创建了当前告警。"
    )
    correlation_status = {
        "WAITING": "waiting",
        "PROCESSING": "processing",
        "FAILED": "failed",
        "COMPLETED": "completed",
    }[correlation.status]
    correlation_title = {
        "WAITING": "等待事故关联",
        "PROCESSING": "正在关联事故",
        "FAILED": "事故关联待重试",
        "COMPLETED": "事故关联已完成",
    }[correlation.status]
    return (
        AlertProcessingStep(
            title="告警已接入",
            detail=f"平台已通过“{source.name}”接收并标准化该信号。",
            status="completed",
        ),
        AlertProcessingStep(
            title=aggregation_title,
            detail=aggregation_detail,
            status="completed",
        ),
        AlertProcessingStep(
            title=correlation_title,
            detail=correlation.explanation,
            status=cast(
                Literal["completed", "processing", "failed", "waiting"], correlation_status
            ),
        ),
    )


def _utc(value: datetime | None) -> datetime | None:
    return None if value is None else value.astimezone(UTC)
