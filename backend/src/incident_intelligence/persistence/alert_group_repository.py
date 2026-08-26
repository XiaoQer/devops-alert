from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from incident_intelligence.ids import new_id
from incident_intelligence.persistence.models import (
    AlertGroupingJobRow,
    AlertGroupMemberRow,
    AlertGroupRow,
    AlertRow,
    IncidentAlertLinkRow,
    SignalEventRow,
)


class AlertGroupRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue_grouping(
        self,
        *,
        alert_id: str,
        alert_cycle: int,
        alert_version: int,
        now: datetime,
    ) -> AlertGroupingJobRow:
        row = AlertGroupingJobRow(
            id=new_id("agj"),
            alert_id=alert_id,
            alert_cycle=alert_cycle,
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

    def claimable_grouping_jobs(
        self, *, now: datetime, limit: int
    ) -> tuple[AlertGroupingJobRow, ...]:
        statement = (
            select(AlertGroupingJobRow)
            .where(
                or_(
                    and_(
                        AlertGroupingJobRow.state == "PENDING",
                        AlertGroupingJobRow.available_at <= now,
                    ),
                    and_(
                        AlertGroupingJobRow.state == "LEASED",
                        AlertGroupingJobRow.lease_expires_at <= now,
                    ),
                )
            )
            .order_by(
                AlertGroupingJobRow.available_at,
                AlertGroupingJobRow.created_at,
                AlertGroupingJobRow.id,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return tuple(self._session.scalars(statement))

    def find_grouping_job(
        self, job_id: str, *, for_update: bool = False
    ) -> AlertGroupingJobRow | None:
        statement = select(AlertGroupingJobRow).where(AlertGroupingJobRow.id == job_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def find_alert_for_update(self, alert_id: str) -> AlertRow | None:
        return self._session.scalar(
            select(AlertRow).where(AlertRow.id == alert_id).with_for_update()
        )

    def find_signal(self, signal_event_id: str) -> SignalEventRow | None:
        return self._session.get(SignalEventRow, signal_event_id)

    def find_member(
        self, alert_id: str, alert_cycle: int, *, for_update: bool = False
    ) -> AlertGroupMemberRow | None:
        statement = select(AlertGroupMemberRow).where(
            AlertGroupMemberRow.alert_id == alert_id,
            AlertGroupMemberRow.alert_cycle == alert_cycle,
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def find_group(self, group_id: str, *, for_update: bool = False) -> AlertGroupRow | None:
        statement = select(AlertGroupRow).where(AlertGroupRow.id == group_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def active_candidates(
        self,
        *,
        service: str,
        environment: str,
        symptom: str,
        limit: int,
    ) -> tuple[AlertGroupRow, ...]:
        return tuple(
            self._session.scalars(
                select(AlertGroupRow)
                .where(
                    AlertGroupRow.state == "ACTIVE",
                    AlertGroupRow.service == service,
                    AlertGroupRow.environment == environment,
                    AlertGroupRow.symptom == symptom,
                )
                .order_by(AlertGroupRow.last_observed_at.desc(), AlertGroupRow.id)
                .limit(limit)
                .with_for_update()
            )
        )

    def find_incident_link(self, alert_id: str) -> IncidentAlertLinkRow | None:
        return self._session.scalar(
            select(IncidentAlertLinkRow).where(IncidentAlertLinkRow.alert_id == alert_id)
        )

    def add_group(self, row: AlertGroupRow) -> None:
        self._session.add(row)
        self._session.flush()

    def add_member(self, row: AlertGroupMemberRow) -> None:
        self._session.add(row)
        self._session.flush()

    def list_members(self, group_id: str) -> tuple[AlertGroupMemberRow, ...]:
        return tuple(
            self._session.scalars(
                select(AlertGroupMemberRow).where(AlertGroupMemberRow.alert_group_id == group_id)
            )
        )

    def find_alert(self, alert_id: str) -> AlertRow | None:
        return self._session.get(AlertRow, alert_id)

    def flush(self) -> None:
        self._session.flush()
