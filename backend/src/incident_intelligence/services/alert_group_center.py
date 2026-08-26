from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.models import Environment, Severity
from incident_intelligence.persistence.models import (
    AlertGroupCorrelationJobRow,
    AlertGroupingJobRow,
    AlertGroupMemberRow,
    AlertGroupRow,
    AlertRow,
    AlertSourceRow,
    IncidentAlertLinkRow,
    IncidentRow,
)

GroupState = Literal["ACTIVE", "RESOLVED"]
StormState = Literal["NORMAL", "STORM"]
SummaryWindow = Literal["1h", "24h", "7d"]


class AlertGroupFilters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    state: GroupState | None = None
    severity: Severity | None = None
    service: str | None = Field(default=None, min_length=1, max_length=128)
    environment: Environment | None = None
    storm_state: StormState | None = None
    incident_linked: bool | None = None
    observed_from: datetime | None = None
    observed_to: datetime | None = None
    query: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10_000)

    @model_validator(mode="after")
    def validate_time_range(self) -> AlertGroupFilters:
        if (
            self.observed_from is not None
            and self.observed_to is not None
            and self.observed_from > self.observed_to
        ):
            raise ValueError("开始时间不得晚于结束时间")
        return self


class GroupIncidentBrief(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    title: str
    state: str
    severity: str


class AlertGroupListItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    title: str
    state: GroupState
    storm_state: StormState
    severity: Severity
    service: str
    environment: Environment
    symptom: str
    active_count: int = Field(ge=0)
    total_count: int = Field(ge=1)
    impacted_resource_count: int = Field(ge=1)
    first_observed_at: datetime
    last_observed_at: datetime
    rule_version: str
    explanation: str
    incident: GroupIncidentBrief | None
    version: int = Field(ge=1)


class AlertGroupPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    items: tuple[AlertGroupListItem, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=10_000)


class AlertGroupSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    window: SummaryWindow
    active_groups: int = Field(ge=0)
    severe_active_groups: int = Field(ge=0)
    active_alerts: int = Field(ge=0)
    storm_groups: int = Field(ge=0)
    resolved_groups: int = Field(ge=0)
    compression_ratio: float = Field(ge=0)
    peak_rate_per_minute: int = Field(ge=0)
    pending_group_jobs: int = Field(ge=0)
    calculated_at: datetime


class NamedCount(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    count: int = Field(ge=0)


class ResourceCount(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    resource_type: str
    resource_name: str
    count: int = Field(ge=1)


class AlertGroupOverview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    group: AlertGroupListItem
    reason_codes: tuple[str, ...] = Field(max_length=10)
    source_distribution: tuple[NamedCount, ...] = Field(max_length=100)
    severity_distribution: tuple[NamedCount, ...] = Field(max_length=10)
    impacted_resources: tuple[ResourceCount, ...] = Field(max_length=20)


class AlertGroupMemberItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    alert_cycle: int = Field(ge=1)
    title: str
    state: str
    severity: Severity
    source_name: str
    service: str
    environment: Environment
    first_observed_at: datetime
    last_observed_at: datetime
    incident_id: str | None
    reason_code: str
    version: int = Field(ge=1)


class AlertGroupMemberPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    items: tuple[AlertGroupMemberItem, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=10_000)


class AlertGroupResourceNotFound(Exception):
    pass


class AlertGroupCenterService:
    def __init__(self, *, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_groups(self, filters: AlertGroupFilters) -> AlertGroupPage:
        query = (
            None if filters.query is None or not filters.query.strip() else filters.query.strip()
        )
        with self._session_factory() as session:
            statement: Any = select(AlertGroupRow, IncidentRow).outerjoin(
                IncidentRow, IncidentRow.id == AlertGroupRow.incident_id
            )
            statement = _group_filters(statement, filters, query)
            rows = session.execute(
                statement.order_by(AlertGroupRow.last_observed_at.desc(), AlertGroupRow.id.desc())
                .limit(filters.limit)
                .offset(filters.offset)
            )
            count_statement = _group_filters(
                select(func.count()).select_from(AlertGroupRow), filters, query
            )
            return AlertGroupPage(
                items=tuple(_group_item(group, incident) for group, incident in rows),
                total=int(session.scalar(count_statement) or 0),
                limit=filters.limit,
                offset=filters.offset,
            )

    def summarize(self, window: SummaryWindow, *, now: datetime) -> AlertGroupSummary:
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
            active_groups = _count(session, AlertGroupRow, AlertGroupRow.state == "ACTIVE")
            severe = _count(
                session,
                AlertGroupRow,
                AlertGroupRow.state == "ACTIVE",
                AlertGroupRow.severity.in_(("critical", "high")),
            )
            active_alerts = _count(session, AlertRow, AlertRow.state == "ACTIVE")
            storms = _count(
                session,
                AlertGroupRow,
                AlertGroupRow.state == "ACTIVE",
                AlertGroupRow.storm_state == "STORM",
            )
            resolved = _count(
                session,
                AlertGroupRow,
                AlertGroupRow.state == "RESOLVED",
                AlertGroupRow.state_changed_at >= cutoff,
            )
            per_minute = (
                select(func.count().label("rate"))
                .select_from(AlertGroupMemberRow)
                .where(AlertGroupMemberRow.joined_at >= cutoff)
                .group_by(func.date_format(AlertGroupMemberRow.joined_at, "%Y-%m-%d %H:%i"))
                .subquery()
            )
            peak = int(session.scalar(select(func.max(per_minute.c.rate))) or 0)
            pending = _count(
                session,
                AlertGroupingJobRow,
                AlertGroupingJobRow.state.in_(("PENDING", "LEASED")),
            ) + _count(
                session,
                AlertGroupCorrelationJobRow,
                AlertGroupCorrelationJobRow.state.in_(("PENDING", "LEASED")),
            )
            return AlertGroupSummary(
                window=window,
                active_groups=active_groups,
                severe_active_groups=severe,
                active_alerts=active_alerts,
                storm_groups=storms,
                resolved_groups=resolved,
                compression_ratio=(
                    0.0 if active_groups == 0 else round(active_alerts / active_groups, 2)
                ),
                peak_rate_per_minute=peak,
                pending_group_jobs=pending,
                calculated_at=calculated_at,
            )

    def get_overview(self, group_id: str) -> AlertGroupOverview:
        with self._session_factory() as session:
            row = session.execute(
                select(AlertGroupRow, IncidentRow)
                .outerjoin(IncidentRow, IncidentRow.id == AlertGroupRow.incident_id)
                .where(AlertGroupRow.id == group_id)
            ).one_or_none()
            if row is None:
                raise AlertGroupResourceNotFound()
            group, incident = row
            sources = session.execute(
                select(AlertSourceRow.name, func.count())
                .select_from(AlertGroupMemberRow)
                .join(AlertRow, AlertRow.id == AlertGroupMemberRow.alert_id)
                .join(AlertSourceRow, AlertSourceRow.id == AlertRow.alert_source_id)
                .where(AlertGroupMemberRow.alert_group_id == group_id)
                .group_by(AlertSourceRow.id, AlertSourceRow.name)
                .order_by(func.count().desc(), AlertSourceRow.name)
                .limit(100)
            )
            severities = session.execute(
                select(AlertGroupMemberRow.current_severity, func.count())
                .where(AlertGroupMemberRow.alert_group_id == group_id)
                .group_by(AlertGroupMemberRow.current_severity)
                .order_by(func.count().desc(), AlertGroupMemberRow.current_severity)
            )
            resources = session.execute(
                select(
                    AlertGroupMemberRow.resource_type,
                    AlertGroupMemberRow.resource_name,
                    func.count(),
                )
                .where(AlertGroupMemberRow.alert_group_id == group_id)
                .group_by(AlertGroupMemberRow.resource_type, AlertGroupMemberRow.resource_name)
                .order_by(func.count().desc(), AlertGroupMemberRow.resource_name)
                .limit(20)
            )
            return AlertGroupOverview(
                group=_group_item(group, incident),
                reason_codes=tuple(group.reason_codes),
                source_distribution=tuple(
                    NamedCount(name=name, count=count) for name, count in sources
                ),
                severity_distribution=tuple(
                    NamedCount(name=name, count=count) for name, count in severities
                ),
                impacted_resources=tuple(
                    ResourceCount(resource_type=kind, resource_name=name, count=count)
                    for kind, name, count in resources
                ),
            )

    def list_members(self, group_id: str, *, limit: int, offset: int) -> AlertGroupMemberPage:
        with self._session_factory() as session:
            if session.get(AlertGroupRow, group_id) is None:
                raise AlertGroupResourceNotFound()
            total = int(
                session.scalar(
                    select(func.count())
                    .select_from(AlertGroupMemberRow)
                    .where(AlertGroupMemberRow.alert_group_id == group_id)
                )
                or 0
            )
            rows = session.execute(
                select(
                    AlertGroupMemberRow,
                    AlertRow,
                    AlertSourceRow,
                    IncidentAlertLinkRow.incident_id,
                )
                .join(AlertRow, AlertRow.id == AlertGroupMemberRow.alert_id)
                .join(AlertSourceRow, AlertSourceRow.id == AlertRow.alert_source_id)
                .outerjoin(IncidentAlertLinkRow, IncidentAlertLinkRow.alert_id == AlertRow.id)
                .where(AlertGroupMemberRow.alert_group_id == group_id)
                .order_by(AlertRow.last_observed_at.desc(), AlertRow.id.desc())
                .limit(limit)
                .offset(offset)
            )
            return AlertGroupMemberPage(
                items=tuple(_member_item(*row) for row in rows),
                total=total,
                limit=limit,
                offset=offset,
            )

    def list_incident_groups(self, incident_id: str, *, limit: int, offset: int) -> AlertGroupPage:
        with self._session_factory() as session:
            if session.get(IncidentRow, incident_id) is None:
                raise AlertGroupResourceNotFound()
            total = _count(session, AlertGroupRow, AlertGroupRow.incident_id == incident_id)
            rows = session.execute(
                select(AlertGroupRow, IncidentRow)
                .join(IncidentRow, IncidentRow.id == AlertGroupRow.incident_id)
                .where(AlertGroupRow.incident_id == incident_id)
                .order_by(AlertGroupRow.last_observed_at.desc(), AlertGroupRow.id.desc())
                .limit(limit)
                .offset(offset)
            )
            return AlertGroupPage(
                items=tuple(_group_item(group, incident) for group, incident in rows),
                total=total,
                limit=limit,
                offset=offset,
            )


def _group_filters(statement: Any, filters: AlertGroupFilters, query: str | None) -> Any:
    result = statement
    values = {
        "state": AlertGroupRow.state,
        "severity": AlertGroupRow.severity,
        "service": AlertGroupRow.service,
        "environment": AlertGroupRow.environment,
        "storm_state": AlertGroupRow.storm_state,
    }
    for name, column in values.items():
        value = getattr(filters, name)
        if value is not None:
            result = result.where(column == value)
    if filters.incident_linked is True:
        result = result.where(AlertGroupRow.incident_id.is_not(None))
    elif filters.incident_linked is False:
        result = result.where(AlertGroupRow.incident_id.is_(None))
    if filters.observed_from is not None:
        result = result.where(AlertGroupRow.last_observed_at >= _utc(filters.observed_from))
    if filters.observed_to is not None:
        result = result.where(AlertGroupRow.last_observed_at <= _utc(filters.observed_to))
    if query is not None:
        result = result.where(
            or_(
                AlertGroupRow.title.contains(query, autoescape=True),
                AlertGroupRow.service.contains(query, autoescape=True),
                AlertGroupRow.symptom.contains(query, autoescape=True),
            )
        )
    return result


def _group_item(group: AlertGroupRow, incident: IncidentRow | None) -> AlertGroupListItem:
    return AlertGroupListItem(
        id=group.id,
        title=group.title,
        state=cast(GroupState, group.state),
        storm_state=cast(StormState, group.storm_state),
        severity=cast(Severity, group.severity),
        service=group.service,
        environment=cast(Environment, group.environment),
        symptom=group.symptom,
        active_count=group.active_count,
        total_count=group.total_count,
        impacted_resource_count=group.impacted_resource_count,
        first_observed_at=group.first_observed_at,
        last_observed_at=group.last_observed_at,
        rule_version=group.rule_version,
        explanation=group.explanation,
        incident=(
            None
            if incident is None
            else GroupIncidentBrief(
                id=incident.id,
                title=incident.title,
                state=incident.state,
                severity=incident.severity,
            )
        ),
        version=group.version,
    )


def _member_item(
    member: AlertGroupMemberRow,
    alert: AlertRow,
    source: AlertSourceRow,
    incident_id: str | None,
) -> AlertGroupMemberItem:
    return AlertGroupMemberItem(
        id=alert.id,
        alert_cycle=member.alert_cycle,
        title=alert.title,
        state=member.current_state,
        severity=cast(Severity, member.current_severity),
        source_name=source.name,
        service=alert.service,
        environment=cast(Environment, alert.environment),
        first_observed_at=alert.first_observed_at,
        last_observed_at=alert.last_observed_at,
        incident_id=incident_id,
        reason_code=member.reason_code,
        version=member.current_alert_version,
    )


def _count(session: Session, row_type: type[object], *criteria: Any) -> int:
    statement: Any = select(func.count()).select_from(row_type).where(*criteria)
    return int(session.scalar(statement) or 0)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
