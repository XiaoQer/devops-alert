from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from incident_intelligence.ids import new_id
from incident_intelligence.persistence.models import (
    AlertRow,
    CorrelationDecisionRow,
    CorrelationJobRow,
    IncidentAlertLinkRow,
    IncidentRow,
    ServiceCatalogEntryRow,
    ServiceDependencyRow,
    SignalEventRow,
)

ACTIVE_INCIDENT_STATES = (
    "DETECTED",
    "TRIAGING",
    "INVESTIGATING",
    "MITIGATING",
    "MONITORING_RECOVERY",
)


class CorrelationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(
        self,
        *,
        alert_id: str,
        alert_version: int,
        now: datetime,
    ) -> CorrelationJobRow:
        row = CorrelationJobRow(
            id=new_id("cjob"),
            alert_id=alert_id,
            alert_version=alert_version,
            state="PENDING",
            attempts=0,
            available_at=now,
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=None,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def claimable_jobs(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[CorrelationJobRow, ...]:
        statement = (
            select(CorrelationJobRow)
            .where(
                or_(
                    and_(
                        CorrelationJobRow.state == "PENDING",
                        CorrelationJobRow.available_at <= now,
                    ),
                    and_(
                        CorrelationJobRow.state == "LEASED",
                        CorrelationJobRow.lease_expires_at <= now,
                    ),
                )
            )
            .order_by(
                CorrelationJobRow.available_at,
                CorrelationJobRow.created_at,
                CorrelationJobRow.id,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return tuple(self._session.scalars(statement))

    def find_job(
        self,
        job_id: str,
        *,
        for_update: bool = False,
    ) -> CorrelationJobRow | None:
        statement = select(CorrelationJobRow).where(CorrelationJobRow.id == job_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def find_alert_for_update(self, alert_id: str) -> AlertRow | None:
        return self._session.scalar(
            select(AlertRow).where(AlertRow.id == alert_id).with_for_update()
        )

    def find_signal(self, signal_event_id: str) -> SignalEventRow | None:
        return self._session.get(SignalEventRow, signal_event_id)

    def find_link_by_alert(self, alert_id: str) -> IncidentAlertLinkRow | None:
        return self._session.scalar(
            select(IncidentAlertLinkRow).where(IncidentAlertLinkRow.alert_id == alert_id)
        )

    def find_incident_for_update(self, incident_id: str) -> IncidentRow | None:
        return self._session.scalar(
            select(IncidentRow).where(IncidentRow.id == incident_id).with_for_update()
        )

    def exact_candidates(
        self,
        *,
        service: str,
        environment: str,
        observed_at: datetime,
        window_seconds: int,
        limit: int,
    ) -> tuple[IncidentRow, ...]:
        window = timedelta(seconds=window_seconds)
        statement = (
            select(IncidentRow)
            .where(
                IncidentRow.state.in_(ACTIVE_INCIDENT_STATES),
                IncidentRow.service == service,
                IncidentRow.environment == environment,
                IncidentRow.detected_at >= observed_at - window,
                IncidentRow.detected_at <= observed_at + window,
            )
            .order_by(IncidentRow.detected_at, IncidentRow.id)
            .limit(limit)
            .with_for_update()
        )
        return tuple(self._session.scalars(statement))

    def dependency_service_names(self, service_id: str) -> tuple[str, ...]:
        edge_rows = self._session.execute(
            select(
                ServiceDependencyRow.caller_service_id,
                ServiceDependencyRow.dependency_service_id,
            ).where(
                ServiceDependencyRow.state == "ACTIVE",
                or_(
                    ServiceDependencyRow.caller_service_id == service_id,
                    ServiceDependencyRow.dependency_service_id == service_id,
                ),
            )
        )
        adjacent_ids = {
            dependency_id if caller_id == service_id else caller_id
            for caller_id, dependency_id in edge_rows
        }
        if not adjacent_ids:
            return ()
        return tuple(
            self._session.scalars(
                select(ServiceCatalogEntryRow.service)
                .where(
                    ServiceCatalogEntryRow.id.in_(adjacent_ids),
                    ServiceCatalogEntryRow.state == "ACTIVE",
                )
                .order_by(ServiceCatalogEntryRow.service)
            )
        )

    def dependency_candidates(
        self,
        *,
        services: tuple[str, ...],
        environment: str,
        observed_at: datetime,
        window_seconds: int,
        limit: int,
    ) -> tuple[tuple[IncidentRow, SignalEventRow], ...]:
        if not services:
            return ()
        window = timedelta(seconds=window_seconds)
        statement = (
            select(IncidentRow, SignalEventRow)
            .join(AlertRow, AlertRow.id == IncidentRow.primary_alert_id)
            .join(SignalEventRow, SignalEventRow.id == AlertRow.signal_event_id)
            .where(
                IncidentRow.state.in_(ACTIVE_INCIDENT_STATES),
                IncidentRow.service.in_(services),
                IncidentRow.environment == environment,
                IncidentRow.detected_at >= observed_at - window,
                IncidentRow.detected_at <= observed_at + window,
            )
            .order_by(IncidentRow.detected_at, IncidentRow.id)
            .limit(limit)
            .with_for_update()
        )
        return tuple(self._session.execute(statement).tuples())

    def add_incident(self, row: IncidentRow) -> None:
        self._session.add(row)
        self._session.flush()

    def add_decision(self, row: CorrelationDecisionRow) -> None:
        self._session.add(row)
        self._session.flush()

    def add_link(self, row: IncidentAlertLinkRow) -> None:
        self._session.add(row)
        self._session.flush()

    def find_latest_job_for_alert(self, alert_id: str) -> CorrelationJobRow | None:
        return self._session.scalar(
            select(CorrelationJobRow)
            .where(CorrelationJobRow.alert_id == alert_id)
            .order_by(CorrelationJobRow.alert_version.desc(), CorrelationJobRow.id.desc())
            .limit(1)
        )

    def find_decision_for_job(self, job_id: str) -> CorrelationDecisionRow | None:
        return self._session.scalar(
            select(CorrelationDecisionRow).where(CorrelationDecisionRow.job_id == job_id)
        )

    def list_jobs(
        self,
        *,
        state: str | None,
        limit: int,
        offset: int,
    ) -> tuple[CorrelationJobRow, ...]:
        statement = select(CorrelationJobRow)
        if state is not None:
            statement = statement.where(CorrelationJobRow.state == state)
        return tuple(
            self._session.scalars(
                statement.order_by(
                    CorrelationJobRow.created_at.desc(), CorrelationJobRow.id.desc()
                )
                .limit(limit)
                .offset(offset)
            )
        )

    def flush(self) -> None:
        self._session.flush()
