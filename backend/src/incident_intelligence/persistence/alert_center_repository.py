from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from incident_intelligence.persistence.models import (
    AlertRow,
    AlertSourceRow,
    CorrelationDecisionRow,
    CorrelationJobRow,
    IncidentAlertLinkRow,
    IncidentRow,
    SignalEventRow,
    SignalIntakeResultRow,
)


@dataclass(frozen=True, slots=True)
class AlertListRecord:
    alert: AlertRow
    source: AlertSourceRow
    incident_id: str | None
    signal_count: int


@dataclass(frozen=True, slots=True)
class AlertOverviewRecord:
    alert: AlertRow
    source: AlertSourceRow


@dataclass(frozen=True, slots=True)
class CorrelationRecord:
    job: CorrelationJobRow | None
    decision: CorrelationDecisionRow | None
    incident: IncidentRow | None


class AlertCenterRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_alerts(
        self,
        *,
        alert_source_id: str | None,
        state: str | None,
        severity: str | None,
        service: str | None,
        environment: str | None,
        incident_linked: bool | None,
        observed_from: datetime | None,
        observed_to: datetime | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> tuple[AlertListRecord, ...]:
        signal_counts = (
            select(
                SignalIntakeResultRow.alert_id.label("alert_id"),
                func.count(func.distinct(SignalIntakeResultRow.signal_event_id)).label(
                    "signal_count"
                ),
            )
            .where(SignalIntakeResultRow.alert_id.is_not(None))
            .group_by(SignalIntakeResultRow.alert_id)
            .subquery()
        )
        statement = (
            select(
                AlertRow,
                AlertSourceRow,
                IncidentAlertLinkRow.incident_id,
                func.coalesce(signal_counts.c.signal_count, 1),
            )
            .join(AlertSourceRow, AlertSourceRow.id == AlertRow.alert_source_id)
            .outerjoin(IncidentAlertLinkRow, IncidentAlertLinkRow.alert_id == AlertRow.id)
            .outerjoin(signal_counts, signal_counts.c.alert_id == AlertRow.id)
        )
        statement = _apply_filters(
            statement,
            alert_source_id=alert_source_id,
            state=state,
            severity=severity,
            service=service,
            environment=environment,
            incident_linked=incident_linked,
            observed_from=observed_from,
            observed_to=observed_to,
            query=query,
        )
        rows = self._session.execute(
            statement.order_by(AlertRow.last_observed_at.desc(), AlertRow.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return tuple(
            AlertListRecord(
                alert=row[0],
                source=row[1],
                incident_id=row[2],
                signal_count=row[3],
            )
            for row in rows
        )

    def count_alerts(
        self,
        *,
        alert_source_id: str | None,
        state: str | None,
        severity: str | None,
        service: str | None,
        environment: str | None,
        incident_linked: bool | None,
        observed_from: datetime | None,
        observed_to: datetime | None,
        query: str | None,
    ) -> int:
        statement = (
            select(func.count())
            .select_from(AlertRow)
            .outerjoin(IncidentAlertLinkRow, IncidentAlertLinkRow.alert_id == AlertRow.id)
        )
        statement = _apply_filters(
            statement,
            alert_source_id=alert_source_id,
            state=state,
            severity=severity,
            service=service,
            environment=environment,
            incident_linked=incident_linked,
            observed_from=observed_from,
            observed_to=observed_to,
            query=query,
        )
        return self._session.scalar(statement) or 0

    def find_alert(self, alert_id: str) -> AlertOverviewRecord | None:
        row = self._session.execute(
            select(AlertRow, AlertSourceRow)
            .join(AlertSourceRow, AlertSourceRow.id == AlertRow.alert_source_id)
            .where(AlertRow.id == alert_id)
        ).one_or_none()
        if row is None:
            return None
        return AlertOverviewRecord(alert=row[0], source=row[1])

    def find_signal(self, signal_event_id: str) -> SignalEventRow | None:
        return self._session.get(SignalEventRow, signal_event_id)

    def alert_signals(self, alert: AlertRow, *, limit: int) -> tuple[SignalEventRow, ...]:
        related_signal_ids = select(SignalIntakeResultRow.signal_event_id).where(
            SignalIntakeResultRow.alert_id == alert.id
        )
        return tuple(
            self._session.scalars(
                select(SignalEventRow)
                .where(
                    or_(
                        SignalEventRow.id == alert.signal_event_id,
                        SignalEventRow.id.in_(related_signal_ids),
                    )
                )
                .order_by(SignalEventRow.observed_at.desc(), SignalEventRow.id.desc())
                .limit(limit)
            )
        )

    def correlation(self, alert_id: str) -> CorrelationRecord:
        job = self._session.scalar(
            select(CorrelationJobRow)
            .where(CorrelationJobRow.alert_id == alert_id)
            .order_by(CorrelationJobRow.alert_version.desc(), CorrelationJobRow.created_at.desc())
            .limit(1)
        )
        if job is None:
            return CorrelationRecord(job=None, decision=None, incident=None)
        decision = self._session.scalar(
            select(CorrelationDecisionRow).where(CorrelationDecisionRow.job_id == job.id)
        )
        link = self._session.scalar(
            select(IncidentAlertLinkRow).where(IncidentAlertLinkRow.alert_id == alert_id)
        )
        incident = None if link is None else self._session.get(IncidentRow, link.incident_id)
        return CorrelationRecord(job=job, decision=decision, incident=incident)

    def summary_counts(self, *, cutoff: datetime) -> tuple[int, int, int, int]:
        active = (
            self._session.scalar(
                select(func.count()).select_from(AlertRow).where(AlertRow.state == "ACTIVE")
            )
            or 0
        )
        severe_active = (
            self._session.scalar(
                select(func.count())
                .select_from(AlertRow)
                .where(AlertRow.state == "ACTIVE", AlertRow.severity.in_(("critical", "high")))
            )
            or 0
        )
        resolved = (
            self._session.scalar(
                select(func.count())
                .select_from(AlertRow)
                .where(AlertRow.state == "RESOLVED", AlertRow.state_changed_at >= cutoff)
            )
            or 0
        )
        unlinked_active = (
            self._session.scalar(
                select(func.count())
                .select_from(AlertRow)
                .outerjoin(IncidentAlertLinkRow, IncidentAlertLinkRow.alert_id == AlertRow.id)
                .where(AlertRow.state == "ACTIVE", IncidentAlertLinkRow.incident_id.is_(None))
            )
            or 0
        )
        return active, severe_active, resolved, unlinked_active

    def active_by_source(self) -> tuple[tuple[AlertSourceRow, int], ...]:
        rows = self._session.execute(
            select(AlertSourceRow, func.count(AlertRow.id))
            .join(AlertRow, AlertRow.alert_source_id == AlertSourceRow.id)
            .where(AlertRow.state == "ACTIVE")
            .group_by(AlertSourceRow.id)
            .order_by(func.count(AlertRow.id).desc(), AlertSourceRow.name, AlertSourceRow.id)
            .limit(100)
        )
        return tuple((row[0], row[1]) for row in rows)


def _apply_filters(statement: Any, **filters: Any) -> Any:
    if filters["alert_source_id"] is not None:
        statement = statement.where(AlertRow.alert_source_id == filters["alert_source_id"])
    if filters["state"] is not None:
        statement = statement.where(AlertRow.state == filters["state"])
    if filters["severity"] is not None:
        statement = statement.where(AlertRow.severity == filters["severity"])
    if filters["service"] is not None:
        statement = statement.where(AlertRow.service == filters["service"])
    if filters["environment"] is not None:
        statement = statement.where(AlertRow.environment == filters["environment"])
    if filters["incident_linked"] is True:
        statement = statement.where(IncidentAlertLinkRow.incident_id.is_not(None))
    elif filters["incident_linked"] is False:
        statement = statement.where(IncidentAlertLinkRow.incident_id.is_(None))
    if filters["observed_from"] is not None:
        statement = statement.where(AlertRow.last_observed_at >= filters["observed_from"])
    if filters["observed_to"] is not None:
        statement = statement.where(AlertRow.last_observed_at <= filters["observed_to"])
    if filters["query"] is not None:
        statement = statement.where(
            or_(
                AlertRow.title.contains(filters["query"], autoescape=True),
                AlertRow.service.contains(filters["query"], autoescape=True),
            )
        )
    return statement
