from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.models import Environment, Severity
from incident_intelligence.persistence.models import (
    AlertEventLifecycleJobRow,
    AlertEventMembershipDecisionRow,
    AlertEventProfileRow,
    AlertGroupCorrelationJobRow,
    AlertGroupDecisionRow,
    AlertGroupingJobRow,
    AlertGroupMemberRow,
    AlertGroupRow,
    AlertRow,
    AlertSourceRow,
    IncidentAlertLinkRow,
    IncidentRow,
)

GroupState = Literal["FORMING", "ACTIVE", "OBSERVING", "CLOSED"]
StormState = Literal["NORMAL", "STORM"]
SummaryWindow = Literal["1h", "24h", "7d"]
EventView = Literal["current", "pending", "history", "all"]


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
    view: EventView = "current"

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
    service: str | None
    problem_type: str
    scope_type: str
    scope_display_name: str
    signature_version: str
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


class CurrentEventSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    active_events: int = Field(ge=0)
    severe_events: int = Field(ge=0)
    active_alerts: int = Field(ge=0)
    storm_events: int = Field(ge=0)
    pending_jobs: int = Field(ge=0)


class WindowEventSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    window: SummaryWindow
    closed_events: int = Field(ge=0)
    raw_alerts: int = Field(ge=0)
    compression_ratio: float = Field(ge=0)
    peak_rate_per_minute: int = Field(ge=0)


class AlertGroupSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    scope: Literal["CURRENT_AND_WINDOW"] = "CURRENT_AND_WINDOW"
    current: CurrentEventSummary
    history: WindowEventSummary
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


class AlertEventProfileView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    profile_version: int = Field(ge=1)
    services: tuple[str, ...] = Field(max_length=100)
    problem_types: tuple[str, ...] = Field(max_length=100)
    symptoms: tuple[str, ...] = Field(max_length=100)
    auto_confirmed_count: int = Field(ge=0)
    manual_confirmed_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    rule_version: str


class ScoreDimensionView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    label: str
    score: int = Field(ge=0)
    maximum: int = Field(ge=0)


class AlertEventGroupingExplanation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    rule_version: str
    total_score: int = Field(ge=0, le=100)
    dimensions: tuple[ScoreDimensionView, ...] = Field(max_length=5)
    reasons: tuple[str, ...] = Field(max_length=10)
    explanation: str


IncidentDecisionStatus = Literal[
    "NOT_EVALUATED",
    "PROCESSING",
    "BELOW_THRESHOLD",
    "SKIPPED",
    "INCIDENT_CREATED",
    "INCIDENT_LINKED",
    "AMBIGUOUS",
    "FAILED",
]


class IncidentDecisionView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: IncidentDecisionStatus
    label: str
    explanation: str
    incident_id: str | None


class AlertEventTimelineNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    occurred_at: datetime
    kind: str
    label: str
    explanation: str
    alert_id: str | None = None


class AlertGroupOverview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    group: AlertGroupListItem
    reason_codes: tuple[str, ...] = Field(max_length=10)
    source_distribution: tuple[NamedCount, ...] = Field(max_length=100)
    severity_distribution: tuple[NamedCount, ...] = Field(max_length=10)
    impacted_resources: tuple[ResourceCount, ...] = Field(max_length=20)
    profile: AlertEventProfileView
    grouping: AlertEventGroupingExplanation
    incident_decision: IncidentDecisionView
    recurrence_count: int = Field(ge=0)
    timeline: tuple[AlertEventTimelineNode, ...] = Field(max_length=200)


class AlertGroupMemberItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    alert_cycle: int = Field(ge=1)
    title: str
    state: str
    severity: Severity
    source_name: str
    service: str | None
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


class PendingAlertEventMemberItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    alert_cycle: int = Field(ge=1)
    title: str
    severity: Severity
    source_name: str
    service: str | None
    environment: Environment
    observed_at: datetime
    total_score: int = Field(ge=0, le=100)
    reason: str
    reason_codes: tuple[str, ...] = Field(max_length=10)


class PendingAlertEventMemberPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    items: tuple[PendingAlertEventMemberItem, ...]
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
            active_groups = _count(
                session,
                AlertGroupRow,
                AlertGroupRow.state.in_(("FORMING", "ACTIVE", "OBSERVING")),
                _group_has_members(),
            )
            severe = _count(
                session,
                AlertGroupRow,
                AlertGroupRow.state.in_(("FORMING", "ACTIVE")),
                AlertGroupRow.severity.in_(("critical", "high")),
                _group_has_members(),
            )
            active_alerts = _count(session, AlertRow, AlertRow.state == "ACTIVE")
            storms = _count(
                session,
                AlertGroupRow,
                AlertGroupRow.state.in_(("FORMING", "ACTIVE")),
                AlertGroupRow.storm_state == "STORM",
                _group_has_members(),
            )
            resolved = _count(
                session,
                AlertGroupRow,
                AlertGroupRow.state == "CLOSED",
                AlertGroupRow.state_changed_at >= cutoff,
                _group_has_members(),
            )
            per_minute = (
                select(func.count().label("rate"))
                .select_from(AlertGroupMemberRow)
                .where(AlertGroupMemberRow.joined_at >= cutoff)
                .group_by(func.date_format(AlertGroupMemberRow.joined_at, "%Y-%m-%d %H:%i"))
                .subquery()
            )
            peak = int(session.scalar(select(func.max(per_minute.c.rate))) or 0)
            pending = (
                _count(
                    session,
                    AlertGroupingJobRow,
                    AlertGroupingJobRow.state.in_(("PENDING", "LEASED")),
                )
                + _count(
                    session,
                    AlertGroupCorrelationJobRow,
                    AlertGroupCorrelationJobRow.state.in_(("PENDING", "LEASED")),
                )
                + _count(
                    session,
                    AlertEventLifecycleJobRow,
                    AlertEventLifecycleJobRow.state.in_(("PENDING", "LEASED")),
                )
            )
            raw_alerts = _count(
                session,
                AlertRow,
                AlertRow.last_observed_at >= cutoff,
            )
            window_events = _count(
                session,
                AlertGroupRow,
                AlertGroupRow.first_observed_at >= cutoff,
                _group_has_members(),
            )
            return AlertGroupSummary(
                current=CurrentEventSummary(
                    active_events=active_groups,
                    severe_events=severe,
                    active_alerts=active_alerts,
                    storm_events=storms,
                    pending_jobs=pending,
                ),
                history=WindowEventSummary(
                    window=window,
                    closed_events=resolved,
                    raw_alerts=raw_alerts,
                    compression_ratio=(
                        0.0 if window_events == 0 else round(raw_alerts / window_events, 2)
                    ),
                    peak_rate_per_minute=peak,
                ),
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
            profile = session.scalar(
                select(AlertEventProfileRow)
                .where(AlertEventProfileRow.alert_group_id == group_id)
                .order_by(AlertEventProfileRow.profile_version.desc())
                .limit(1)
            )
            if profile is None:
                raise AlertGroupResourceNotFound()
            membership_decisions = tuple(
                session.scalars(
                    select(AlertEventMembershipDecisionRow)
                    .where(AlertEventMembershipDecisionRow.selected_group_id == group_id)
                    .order_by(
                        AlertEventMembershipDecisionRow.created_at.desc(),
                        AlertEventMembershipDecisionRow.id.desc(),
                    )
                    .limit(200)
                )
            )
            latest_membership = membership_decisions[0] if membership_decisions else None
            incident_decision = _incident_decision(session, group)
            recurrence_count = _count(
                session,
                AlertGroupRow,
                AlertGroupRow.problem_key == group.problem_key,
                AlertGroupRow.id != group.id,
                _group_has_members(),
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
                profile=_profile_view(profile),
                grouping=_grouping_explanation(latest_membership, group),
                incident_decision=incident_decision,
                recurrence_count=recurrence_count,
                timeline=tuple(_timeline_node(item) for item in reversed(membership_decisions)),
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

    def list_pending_members(
        self, group_id: str, *, limit: int, offset: int
    ) -> PendingAlertEventMemberPage:
        with self._session_factory() as session:
            if session.get(AlertGroupRow, group_id) is None:
                raise AlertGroupResourceNotFound()
            candidate = _pending_candidate_for(group_id)
            total = _count(
                session,
                AlertEventMembershipDecisionRow,
                AlertEventMembershipDecisionRow.state == "PENDING",
                candidate,
            )
            rows = session.execute(
                select(
                    AlertEventMembershipDecisionRow,
                    AlertRow,
                    AlertSourceRow,
                )
                .join(AlertRow, AlertRow.id == AlertEventMembershipDecisionRow.alert_id)
                .join(AlertSourceRow, AlertSourceRow.id == AlertRow.alert_source_id)
                .where(
                    AlertEventMembershipDecisionRow.state == "PENDING",
                    candidate,
                )
                .order_by(
                    AlertEventMembershipDecisionRow.created_at.desc(),
                    AlertEventMembershipDecisionRow.id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
            return PendingAlertEventMemberPage(
                items=tuple(
                    _pending_member_item(decision, alert, source, group_id)
                    for decision, alert, source in rows
                ),
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
    result = statement.where(_group_has_members())
    if filters.state is None:
        if filters.view == "current":
            result = result.where(AlertGroupRow.state != "CLOSED")
        elif filters.view == "pending":
            result = result.where(
                exists(
                    select(AlertEventMembershipDecisionRow.id).where(
                        AlertEventMembershipDecisionRow.state == "PENDING",
                        func.json_contains(
                            AlertEventMembershipDecisionRow.candidate_group_ids,
                            func.json_quote(AlertGroupRow.id),
                        )
                        == 1,
                    )
                )
            )
        elif filters.view == "history":
            result = result.where(AlertGroupRow.state == "CLOSED")
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
                AlertGroupRow.problem_type.contains(query, autoescape=True),
                AlertGroupRow.scope_display_name.contains(query, autoescape=True),
                AlertGroupRow.symptom.contains(query, autoescape=True),
            )
        )
    return result


def _group_has_members() -> Any:
    return exists(
        select(AlertGroupMemberRow.alert_id).where(
            AlertGroupMemberRow.alert_group_id == AlertGroupRow.id
        )
    )


def _group_item(group: AlertGroupRow, incident: IncidentRow | None) -> AlertGroupListItem:
    return AlertGroupListItem(
        id=group.id,
        title=group.title,
        state=cast(GroupState, group.state),
        storm_state=cast(StormState, group.storm_state),
        severity=cast(Severity, group.severity),
        service=group.service,
        problem_type=group.problem_type,
        scope_type=group.scope_type,
        scope_display_name=group.scope_display_name,
        signature_version=group.signature_version,
        environment=group.environment,
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
        environment=alert.environment,
        first_observed_at=alert.first_observed_at,
        last_observed_at=alert.last_observed_at,
        incident_id=incident_id,
        reason_code=member.reason_code,
        version=member.current_alert_version,
    )


def _pending_candidate_for(group_id: str) -> Any:
    return (
        func.json_contains(
            AlertEventMembershipDecisionRow.candidate_group_ids,
            func.json_quote(group_id),
        )
        == 1
    )


def _pending_member_item(
    decision: AlertEventMembershipDecisionRow,
    alert: AlertRow,
    source: AlertSourceRow,
    group_id: str,
) -> PendingAlertEventMemberItem:
    raw = decision.scores.get(group_id)
    score = raw if isinstance(raw, dict) else {}
    return PendingAlertEventMemberItem(
        id=alert.id,
        alert_cycle=alert.cycle,
        title=alert.title,
        severity=cast(Severity, alert.severity),
        source_name=source.name,
        service=alert.service,
        environment=alert.environment,
        observed_at=alert.last_observed_at,
        total_score=min(100, _safe_score(score.get("total_score"))),
        reason=decision.explanation,
        reason_codes=tuple(decision.reason_codes[:10]),
    )


def _profile_view(profile: AlertEventProfileRow) -> AlertEventProfileView:
    return AlertEventProfileView(
        profile_version=profile.profile_version,
        services=tuple(profile.services[:100]),
        problem_types=tuple(profile.problem_types[:100]),
        symptoms=tuple(profile.symptoms[:100]),
        auto_confirmed_count=profile.auto_confirmed_count,
        manual_confirmed_count=profile.manual_confirmed_count,
        pending_count=profile.pending_count,
        rule_version=profile.rule_version,
    )


def _grouping_explanation(
    decision: AlertEventMembershipDecisionRow | None,
    group: AlertGroupRow,
) -> AlertEventGroupingExplanation:
    if decision is None:
        return AlertEventGroupingExplanation(
            rule_version=group.rule_version,
            total_score=0,
            dimensions=(),
            reasons=tuple(group.reason_codes[:10]),
            explanation=group.explanation,
        )
    raw = decision.scores.get(group.id)
    score = raw if isinstance(raw, dict) else {}
    dimensions = tuple(
        ScoreDimensionView(
            name=name,
            label=label,
            score=_safe_score(score.get(name)),
            maximum=maximum,
        )
        for name, label, maximum in (
            ("entity_service_score", "实体与服务", 35),
            ("topology_score", "调用关系", 25),
            ("temporal_score", "发生时间", 20),
            ("semantic_score", "文本语义", 15),
            ("history_score", "历史反馈", 5),
        )
    )
    return AlertEventGroupingExplanation(
        rule_version=decision.rule_version,
        total_score=min(100, sum(item.score for item in dimensions)),
        dimensions=dimensions,
        reasons=tuple(decision.reason_codes[:10]),
        explanation=decision.explanation,
    )


def _safe_score(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _timeline_node(decision: AlertEventMembershipDecisionRow) -> AlertEventTimelineNode:
    labels = {
        "AUTO_CONFIRMED": "系统自动归入事件",
        "MANUAL_CONFIRMED": "人工确认归入事件",
        "REMOVED": "人工移出事件",
        "PENDING": "等待人工确认",
    }
    return AlertEventTimelineNode(
        occurred_at=decision.created_at,
        kind=decision.state,
        label=labels.get(decision.state, "归组状态发生变化"),
        explanation=decision.explanation,
        alert_id=decision.alert_id,
    )


def _incident_decision(session: Session, group: AlertGroupRow) -> IncidentDecisionView:
    job = session.scalar(
        select(AlertGroupCorrelationJobRow)
        .where(AlertGroupCorrelationJobRow.alert_group_id == group.id)
        .order_by(
            AlertGroupCorrelationJobRow.target_group_version.desc(),
            AlertGroupCorrelationJobRow.created_at.desc(),
        )
        .limit(1)
    )
    if job is None:
        return IncidentDecisionView(
            status="NOT_EVALUATED",
            label="尚未进行事故判定",
            explanation="该事件还没有进入事故判定流程。",
            incident_id=None,
        )
    if job.state in {"PENDING", "LEASED"}:
        return IncidentDecisionView(
            status="PROCESSING",
            label="正在进行事故判定",
            explanation="系统正在判断该事件是否需要进入事故中心。",
            incident_id=group.incident_id,
        )
    if job.state == "FAILED":
        return IncidentDecisionView(
            status="FAILED",
            label="事故判定失败",
            explanation="本次事故判定未完成。可安全重试或人工处理。",
            incident_id=group.incident_id,
        )
    decision = session.scalar(
        select(AlertGroupDecisionRow).where(AlertGroupDecisionRow.job_id == job.id)
    )
    if decision is None:
        return IncidentDecisionView(
            status="FAILED",
            label="事故判定结果缺失",
            explanation="任务已结束。不过没有找到对应的判定记录。",
            incident_id=group.incident_id,
        )
    status, label = _incident_outcome(decision.outcome, decision.reason_codes)
    return IncidentDecisionView(
        status=status,
        label=label,
        explanation=decision.explanation,
        incident_id=decision.incident_id,
    )


def _incident_outcome(outcome: str, reason_codes: list[str]) -> tuple[IncidentDecisionStatus, str]:
    if outcome == "CREATED_AMBIGUOUS":
        return "AMBIGUOUS", "已创建事故并保留多个候选关系"
    if outcome.startswith("CREATED_"):
        return "INCIDENT_CREATED", "已创建事故"
    if outcome.startswith("LINKED_"):
        return "INCIDENT_LINKED", "已关联已有事故"
    if "severity_below_threshold" in reason_codes:
        return "BELOW_THRESHOLD", "未达到事故处置门槛"
    return "SKIPPED", "无需进入事故中心"


def _count(session: Session, row_type: type[object], *criteria: Any) -> int:
    statement: Any = select(func.count()).select_from(row_type).where(*criteria)
    return int(session.scalar(statement) or 0)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
