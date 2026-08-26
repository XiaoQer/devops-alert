from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.enums import IncidentState
from incident_intelligence.domain.incident_operations import (
    allowed_actions,
    allowed_transitions,
    primary_action,
)
from incident_intelligence.persistence.incident_center_repository import (
    IncidentCenterRepository,
)
from incident_intelligence.persistence.models import IncidentActivityRow


class IncidentListQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment: str | None = None
    state: str | None = None
    query: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10_000)


class IncidentListItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str
    severity: str
    state: str
    service: str
    environment: str
    assignee: str | None
    owner_team: str | None
    detected_at: datetime
    last_activity_at: datetime
    alert_count: int
    version: int


class IncidentListResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[IncidentListItem, ...]
    total: int
    limit: int
    offset: int


class IncidentAlertView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str
    state: str
    severity: str
    source: str
    first_observed_at: datetime
    last_observed_at: datetime
    version: int


class IncidentCorrelationView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: str
    rule_version: str
    reason_codes: tuple[str, ...] = Field(max_length=10)
    explanation: str
    created_at: datetime


class IncidentTimelineEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: str
    occurred_at: datetime
    title: str
    detail: str


class IncidentActivityView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: str
    actor: str
    from_state: str | None
    to_state: str | None
    note_category: str | None
    message: str | None
    resolution_category: str | None
    resolution_actions: str | None
    root_cause: str | None
    incident_version: int
    created_at: datetime


class IncidentPrimaryActionView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: str
    target_state: str | None


class IncidentOverview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str
    severity: str
    state: str
    service: str
    environment: str
    assignee: str | None
    claimed_at: datetime | None
    owner_team: str | None
    detected_at: datetime
    state_changed_at: datetime
    resolved_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    version: int
    alerts: tuple[IncidentAlertView, ...]
    alerts_truncated: bool
    correlation: IncidentCorrelationView | None
    activities: tuple[IncidentActivityView, ...]
    activities_truncated: bool
    allowed_actions: tuple[str, ...]
    allowed_transitions: tuple[str, ...]
    primary_action: IncidentPrimaryActionView | None
    timeline: tuple[IncidentTimelineEvent, ...]


class IncidentResourceNotFound(Exception):
    pass


class IncidentCenterService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
    ) -> None:
        self._session_factory = session_factory

    def list_incidents(self, query: IncidentListQuery) -> IncidentListResult:
        with self._session_factory() as session:
            repository = IncidentCenterRepository(session)
            records = repository.list_incidents(
                environment=query.environment,
                state=query.state,
                query=query.query,
                limit=query.limit,
                offset=query.offset,
            )
            total = repository.count_incidents(
                environment=query.environment,
                state=query.state,
                query=query.query,
            )
            return IncidentListResult(
                items=tuple(
                    IncidentListItem(
                        id=record.incident.id,
                        title=record.incident.title,
                        severity=record.incident.severity,
                        state=record.incident.state,
                        service=record.incident.service,
                        environment=record.incident.environment,
                        assignee=record.incident.assignee,
                        owner_team=record.owner_team,
                        detected_at=record.incident.detected_at,
                        last_activity_at=record.last_activity_at,
                        alert_count=record.alert_count,
                        version=record.incident.version,
                    )
                    for record in records
                ),
                total=total,
                limit=query.limit,
                offset=query.offset,
            )

    def get_overview(self, incident_id: str, *, actor: str) -> IncidentOverview:
        with self._session_factory() as session:
            repository = IncidentCenterRepository(session)
            incident = repository.find_incident(incident_id)
            if incident is None:
                raise IncidentResourceNotFound()
            linked = repository.linked_alerts(incident_id, limit=101)
            visible = linked[:100]
            decision = repository.latest_decision(incident_id)
            activities = repository.activities(incident_id, limit=201)
            visible_activities = activities[:200]
            timeline = [
                IncidentTimelineEvent(
                    id=f"created:{incident.id}",
                    kind="incident_created",
                    occurred_at=incident.created_at,
                    title="创建事故",
                    detail="系统根据已持久化告警创建事故",
                )
            ]
            timeline.extend(
                IncidentTimelineEvent(
                    id=f"linked:{record.alert.id}",
                    kind="alert_linked",
                    occurred_at=record.link.linked_at,
                    title="关联告警",
                    detail=record.alert.title,
                )
                for record in visible
            )
            timeline.extend(_activity_timeline(activity) for activity in visible_activities)
            timeline.sort(
                key=lambda item: (
                    item.occurred_at,
                    0 if item.kind == "incident_created" else 1,
                    item.id,
                )
            )
            return IncidentOverview(
                id=incident.id,
                title=incident.title,
                severity=incident.severity,
                state=incident.state,
                service=incident.service,
                environment=incident.environment,
                assignee=incident.assignee,
                claimed_at=incident.claimed_at,
                owner_team=repository.find_owner_team(incident),
                detected_at=incident.detected_at,
                state_changed_at=incident.state_changed_at,
                resolved_at=incident.resolved_at,
                closed_at=incident.closed_at,
                created_at=incident.created_at,
                version=incident.version,
                alerts=tuple(
                    IncidentAlertView(
                        id=record.alert.id,
                        title=record.alert.title,
                        state=record.alert.state,
                        severity=record.alert.severity,
                        source=record.alert.source,
                        first_observed_at=record.alert.first_observed_at,
                        last_observed_at=record.alert.last_observed_at,
                        version=record.alert.version,
                    )
                    for record in visible
                ),
                alerts_truncated=len(linked) > 100,
                correlation=(
                    None
                    if decision is None
                    else IncidentCorrelationView(
                        outcome=decision.outcome,
                        rule_version=decision.rule_version,
                        reason_codes=tuple(decision.reason_codes),
                        explanation=decision.explanation,
                        created_at=decision.created_at,
                    )
                ),
                activities=tuple(
                    IncidentActivityView(
                        id=activity.id,
                        kind=activity.kind,
                        actor=activity.actor,
                        from_state=activity.from_state,
                        to_state=activity.to_state,
                        note_category=activity.note_category,
                        message=activity.message,
                        resolution_category=activity.resolution_category,
                        resolution_actions=activity.resolution_actions,
                        root_cause=activity.root_cause,
                        incident_version=activity.incident_version,
                        created_at=activity.created_at,
                    )
                    for activity in visible_activities
                ),
                activities_truncated=len(activities) > 200,
                allowed_actions=tuple(
                    action.value
                    for action in allowed_actions(
                        IncidentState(incident.state), incident.assignee, actor
                    )
                ),
                allowed_transitions=tuple(
                    state.value for state in allowed_transitions(IncidentState(incident.state))
                ),
                primary_action=(
                    None
                    if (suggested := primary_action(IncidentState(incident.state))) is None
                    else IncidentPrimaryActionView(
                        action=suggested.action.value,
                        target_state=(
                            None if suggested.target_state is None else suggested.target_state.value
                        ),
                    )
                ),
                timeline=tuple(timeline),
            )


def _activity_timeline(activity: IncidentActivityRow) -> IncidentTimelineEvent:
    kind = activity.kind
    titles = {
        "INCIDENT_CLAIMED": "事故已认领",
        "INCIDENT_RELEASED": "已解除认领",
        "STATE_TRANSITIONED": "处置阶段已更新",
        "NOTE_ADDED": "添加处置记录",
        "INCIDENT_RESOLVED": "事故已解决",
        "INCIDENT_REOPENED": "事故已重新打开",
        "INCIDENT_CLOSED": "事故已关闭",
    }
    detail = activity.message
    if not detail:
        if kind == "INCIDENT_CLAIMED":
            detail = f"{activity.actor} 开始负责本次事故"
        elif kind == "INCIDENT_RELEASED":
            detail = f"{activity.actor} 解除事故认领"
        elif activity.from_state and activity.to_state:
            detail = f"{activity.from_state} → {activity.to_state}"
        else:
            detail = "处置状态已更新"
    return IncidentTimelineEvent(
        id=activity.id,
        kind=kind.lower(),
        occurred_at=activity.created_at,
        title=titles[kind],
        detail=detail,
    )
