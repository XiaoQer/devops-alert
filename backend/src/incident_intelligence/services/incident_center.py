from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.ids import new_id
from incident_intelligence.persistence.incident_center_repository import (
    IncidentCenterRepository,
)
from incident_intelligence.persistence.repositories import RecordRepositories


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
    created_at: datetime
    version: int
    alerts: tuple[IncidentAlertView, ...]
    alerts_truncated: bool
    correlation: IncidentCorrelationView | None
    timeline: tuple[IncidentTimelineEvent, ...]


class IncidentClaimResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    assignee: str
    claimed_at: datetime
    version: int


class IncidentResourceNotFound(Exception):
    pass


class IncidentAlreadyClaimed(Exception):
    pass


class IncidentNotClaimable(Exception):
    pass


class IncidentCenterService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

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

    def get_overview(self, incident_id: str) -> IncidentOverview:
        with self._session_factory() as session:
            repository = IncidentCenterRepository(session)
            incident = repository.find_incident(incident_id)
            if incident is None:
                raise IncidentResourceNotFound()
            linked = repository.linked_alerts(incident_id, limit=101)
            visible = linked[:100]
            decision = repository.latest_decision(incident_id)
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
            if incident.assignee is not None and incident.claimed_at is not None:
                timeline.append(
                    IncidentTimelineEvent(
                        id=f"claimed:{incident.id}:{incident.version}",
                        kind="incident_claimed",
                        occurred_at=incident.claimed_at,
                        title="事故已认领",
                        detail=incident.assignee,
                    )
                )
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
                timeline=tuple(timeline),
            )

    def claim(self, incident_id: str, *, actor: str, request_id: str) -> IncidentClaimResult:
        now = self._clock().astimezone(UTC)
        with self._session_factory.begin() as session:
            repository = IncidentCenterRepository(session)
            incident = repository.find_incident(incident_id, for_update=True)
            if incident is None:
                raise IncidentResourceNotFound()
            if incident.state in {"RESOLVED", "CLOSED"}:
                raise IncidentNotClaimable()
            if incident.assignee is not None:
                if incident.assignee != actor or incident.claimed_at is None:
                    raise IncidentAlreadyClaimed()
                return IncidentClaimResult(
                    id=incident.id,
                    assignee=incident.assignee,
                    claimed_at=incident.claimed_at,
                    version=incident.version,
                )
            incident.assignee = actor
            incident.claimed_at = now
            incident.version += 1
            RecordRepositories(session).add_audit(
                audit_id=new_id("aud"),
                actor=actor,
                action="incident.claimed",
                resource_type="incident",
                resource_id=incident.id,
                request_id=request_id,
                details={"reason_code": "manual_claim_requested"},
                created_at=now,
            )
            session.flush()
            return IncidentClaimResult(
                id=incident.id,
                assignee=actor,
                claimed_at=now,
                version=incident.version,
            )
