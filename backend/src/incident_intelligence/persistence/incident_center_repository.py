from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, case, cast, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from incident_intelligence.persistence.models import (
    AlertRow,
    CorrelationDecisionRow,
    IncidentActivityRow,
    IncidentAlertLinkRow,
    IncidentRow,
    ServiceCatalogEntryRow,
)
from incident_intelligence.persistence.types import UtcDateTime

ACTIVE_INCIDENT_STATES = (
    "DETECTED",
    "TRIAGING",
    "INVESTIGATING",
    "MITIGATING",
    "MONITORING_RECOVERY",
)


@dataclass(frozen=True, slots=True)
class IncidentListRecord:
    incident: IncidentRow
    owner_team: str | None
    alert_count: int
    last_activity_at: datetime


@dataclass(frozen=True, slots=True)
class LinkedAlertRecord:
    link: IncidentAlertLinkRow
    alert: AlertRow


class IncidentCenterRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_incidents(
        self,
        *,
        environment: str | None,
        state: str | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> tuple[IncidentListRecord, ...]:
        aggregate = (
            select(
                IncidentAlertLinkRow.incident_id.label("incident_id"),
                func.count(IncidentAlertLinkRow.alert_id).label("alert_count"),
                func.max(AlertRow.last_observed_at).label("last_activity_at"),
            )
            .join(AlertRow, AlertRow.id == IncidentAlertLinkRow.alert_id)
            .group_by(IncidentAlertLinkRow.incident_id)
            .subquery()
        )
        activity_aggregate = (
            select(
                IncidentActivityRow.incident_id.label("incident_id"),
                func.max(IncidentActivityRow.created_at).label("last_activity_at"),
            )
            .group_by(IncidentActivityRow.incident_id)
            .subquery()
        )
        statement = (
            select(
                IncidentRow,
                ServiceCatalogEntryRow.owner_team,
                func.coalesce(aggregate.c.alert_count, 0),
                cast(
                    func.greatest(
                        IncidentRow.detected_at,
                        func.coalesce(aggregate.c.last_activity_at, IncidentRow.detected_at),
                        func.coalesce(
                            activity_aggregate.c.last_activity_at,
                            IncidentRow.detected_at,
                        ),
                    ),
                    UtcDateTime(),
                ),
            )
            .outerjoin(
                ServiceCatalogEntryRow,
                and_(
                    ServiceCatalogEntryRow.service == IncidentRow.service,
                    ServiceCatalogEntryRow.environment == IncidentRow.environment,
                    ServiceCatalogEntryRow.state == "ACTIVE",
                ),
            )
            .outerjoin(aggregate, aggregate.c.incident_id == IncidentRow.id)
            .outerjoin(
                activity_aggregate,
                activity_aggregate.c.incident_id == IncidentRow.id,
            )
        )
        statement = self._filters(statement, environment, state, query)
        rows = self._session.execute(
            statement.order_by(
                case((IncidentRow.state.in_(ACTIVE_INCIDENT_STATES), 0), else_=1),
                IncidentRow.detected_at.desc(),
                IncidentRow.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return tuple(
            IncidentListRecord(
                incident=incident,
                owner_team=owner_team,
                alert_count=int(alert_count),
                last_activity_at=last_activity_at,
            )
            for incident, owner_team, alert_count, last_activity_at in rows
        )

    def count_incidents(
        self,
        *,
        environment: str | None,
        state: str | None,
        query: str | None,
    ) -> int:
        statement = select(func.count(IncidentRow.id)).outerjoin(
            ServiceCatalogEntryRow,
            and_(
                ServiceCatalogEntryRow.service == IncidentRow.service,
                ServiceCatalogEntryRow.environment == IncidentRow.environment,
                ServiceCatalogEntryRow.state == "ACTIVE",
            ),
        )
        statement = self._filters(statement, environment, state, query)
        return int(self._session.scalar(statement) or 0)

    def find_incident(self, incident_id: str, *, for_update: bool = False) -> IncidentRow | None:
        statement = select(IncidentRow).where(IncidentRow.id == incident_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def find_owner_team(self, incident: IncidentRow) -> str | None:
        return self._session.scalar(
            select(ServiceCatalogEntryRow.owner_team).where(
                ServiceCatalogEntryRow.service == incident.service,
                ServiceCatalogEntryRow.environment == incident.environment,
                ServiceCatalogEntryRow.state == "ACTIVE",
            )
        )

    def linked_alerts(self, incident_id: str, *, limit: int) -> tuple[LinkedAlertRecord, ...]:
        rows = self._session.execute(
            select(IncidentAlertLinkRow, AlertRow)
            .join(AlertRow, AlertRow.id == IncidentAlertLinkRow.alert_id)
            .where(IncidentAlertLinkRow.incident_id == incident_id)
            .order_by(IncidentAlertLinkRow.linked_at, IncidentAlertLinkRow.alert_id)
            .limit(limit)
        )
        return tuple(LinkedAlertRecord(link=link, alert=alert) for link, alert in rows)

    def activities(self, incident_id: str, *, limit: int) -> tuple[IncidentActivityRow, ...]:
        return tuple(
            self._session.scalars(
                select(IncidentActivityRow)
                .where(IncidentActivityRow.incident_id == incident_id)
                .order_by(IncidentActivityRow.created_at, IncidentActivityRow.id)
                .limit(limit)
            )
        )

    def latest_decision(self, incident_id: str) -> CorrelationDecisionRow | None:
        return self._session.scalar(
            select(CorrelationDecisionRow)
            .where(CorrelationDecisionRow.incident_id == incident_id)
            .order_by(
                case(
                    (
                        CorrelationDecisionRow.outcome.in_(
                            ("LINKED_EXACT_SERVICE", "LINKED_EXISTING")
                        ),
                        0,
                    ),
                    else_=1,
                ),
                CorrelationDecisionRow.created_at.desc(),
                CorrelationDecisionRow.id.desc(),
            )
            .limit(1)
        )

    @staticmethod
    def _filters(
        statement: Select[Any],
        environment: str | None,
        state: str | None,
        query: str | None,
    ) -> Select[Any]:
        if environment is not None:
            statement = statement.where(IncidentRow.environment == environment)
        if state is not None:
            statement = statement.where(IncidentRow.state == state)
        if query:
            statement = statement.where(
                or_(
                    IncidentRow.title.contains(query, autoescape=True),
                    IncidentRow.service.contains(query, autoescape=True),
                    ServiceCatalogEntryRow.owner_team.contains(query, autoescape=True),
                )
            )
        return statement
