from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import Session

from incident_intelligence.ids import new_id
from incident_intelligence.persistence.models import (
    AlertGroupCorrelationJobRow,
    AlertGroupDecisionRow,
    AlertGroupingJobRow,
    AlertGroupMemberRow,
    AlertGroupRow,
    AlertRow,
    CorrelationJobRow,
    IncidentAlertLinkRow,
    IncidentRow,
    SignalEventRow,
)


@dataclass(frozen=True, slots=True)
class AlertGroupAggregate:
    total_count: int
    active_count: int
    impacted_resource_count: int
    first_observed_at: datetime
    last_observed_at: datetime
    last_member_at: datetime
    recent_member_count: int
    representative_alert_id: str
    representative_title: str
    representative_severity: str


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

    def backfill_candidates(self, *, limit: int) -> tuple[AlertRow, ...]:
        has_current_member = exists(
            select(AlertGroupMemberRow.alert_id).where(
                AlertGroupMemberRow.alert_id == AlertRow.id,
                AlertGroupMemberRow.alert_cycle == AlertRow.cycle,
            )
        )
        has_active_legacy_job = exists(
            select(CorrelationJobRow.id).where(
                CorrelationJobRow.alert_id == AlertRow.id,
                CorrelationJobRow.state.in_(("PENDING", "LEASED")),
            )
        )
        has_current_grouping_job = exists(
            select(AlertGroupingJobRow.id).where(
                AlertGroupingJobRow.alert_id == AlertRow.id,
                AlertGroupingJobRow.alert_version == AlertRow.version,
            )
        )
        return tuple(
            self._session.scalars(
                select(AlertRow)
                .where(
                    ~has_current_member,
                    ~has_active_legacy_job,
                    ~has_current_grouping_job,
                )
                .order_by(AlertRow.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )

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
        entity_key: str,
        environment: str,
        symptom: str,
        limit: int,
    ) -> tuple[AlertGroupRow, ...]:
        return tuple(
            self._session.scalars(
                select(AlertGroupRow)
                .where(
                    AlertGroupRow.state == "ACTIVE",
                    AlertGroupRow.entity_key == entity_key,
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

    def aggregate_group(
        self, group_id: str, *, recent_since: datetime
    ) -> AlertGroupAggregate | None:
        row = self._session.execute(
            select(
                func.count(AlertGroupMemberRow.alert_id),
                func.sum(case((AlertGroupMemberRow.current_state == "ACTIVE", 1), else_=0)),
                func.count(func.distinct(AlertGroupMemberRow.resource_key)),
                func.min(AlertRow.first_observed_at),
                func.max(AlertRow.last_observed_at),
                func.max(AlertGroupMemberRow.joined_at),
                func.sum(case((AlertGroupMemberRow.joined_at >= recent_since, 1), else_=0)),
            )
            .join(AlertRow, AlertRow.id == AlertGroupMemberRow.alert_id)
            .where(AlertGroupMemberRow.alert_group_id == group_id)
        ).one()
        if not row[0]:
            return None
        representative = self._session.execute(
            select(
                AlertGroupMemberRow.alert_id,
                AlertRow.title,
                AlertGroupMemberRow.current_severity,
            )
            .join(AlertRow, AlertRow.id == AlertGroupMemberRow.alert_id)
            .where(AlertGroupMemberRow.alert_group_id == group_id)
            .order_by(
                case(
                    (AlertGroupMemberRow.current_severity == "critical", 3),
                    (AlertGroupMemberRow.current_severity == "high", 2),
                    (AlertGroupMemberRow.current_severity == "medium", 1),
                    else_=0,
                ).desc(),
                AlertRow.last_observed_at.desc(),
                AlertGroupMemberRow.alert_id.desc(),
            )
            .limit(1)
        ).one()
        return AlertGroupAggregate(
            total_count=int(row[0]),
            active_count=int(row[1] or 0),
            impacted_resource_count=int(row[2]),
            first_observed_at=cast(datetime, row[3]),
            last_observed_at=cast(datetime, row[4]),
            last_member_at=cast(datetime, row[5]),
            recent_member_count=int(row[6] or 0),
            representative_alert_id=cast(str, representative[0]),
            representative_title=cast(str, representative[1]),
            representative_severity=cast(str, representative[2]),
        )

    def find_alert(self, alert_id: str) -> AlertRow | None:
        return self._session.get(AlertRow, alert_id)

    def find_active_correlation_job(
        self, group_id: str, *, for_update: bool = False
    ) -> AlertGroupCorrelationJobRow | None:
        statement = select(AlertGroupCorrelationJobRow).where(
            AlertGroupCorrelationJobRow.alert_group_id == group_id,
            AlertGroupCorrelationJobRow.active_slot == 1,
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def schedule_correlation(
        self, group: AlertGroupRow, *, target_version: int, now: datetime
    ) -> AlertGroupCorrelationJobRow | None:
        group.desired_correlation_version = max(group.desired_correlation_version, target_version)
        active = self.find_active_correlation_job(group.id, for_update=True)
        if active is not None:
            if active.state == "PENDING":
                active.target_group_version = max(active.target_group_version, target_version)
                active.available_at = min(active.available_at, now)
                active.updated_at = now
            self._session.flush()
            return active
        row = AlertGroupCorrelationJobRow(
            id=new_id("gcj"),
            alert_group_id=group.id,
            target_group_version=target_version,
            state="PENDING",
            active_slot=1,
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

    def claimable_correlation_jobs(
        self, *, now: datetime, limit: int
    ) -> tuple[AlertGroupCorrelationJobRow, ...]:
        statement = (
            select(AlertGroupCorrelationJobRow)
            .where(
                or_(
                    and_(
                        AlertGroupCorrelationJobRow.state == "PENDING",
                        AlertGroupCorrelationJobRow.available_at <= now,
                    ),
                    and_(
                        AlertGroupCorrelationJobRow.state == "LEASED",
                        AlertGroupCorrelationJobRow.lease_expires_at <= now,
                    ),
                )
            )
            .order_by(
                AlertGroupCorrelationJobRow.available_at,
                AlertGroupCorrelationJobRow.created_at,
                AlertGroupCorrelationJobRow.id,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return tuple(self._session.scalars(statement))

    def find_correlation_job(
        self, job_id: str, *, for_update: bool = False
    ) -> AlertGroupCorrelationJobRow | None:
        statement = select(AlertGroupCorrelationJobRow).where(
            AlertGroupCorrelationJobRow.id == job_id
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def add_group_decision(self, row: AlertGroupDecisionRow) -> None:
        self._session.add(row)
        self._session.flush()

    def add_incident(self, row: IncidentRow) -> None:
        self._session.add(row)
        self._session.flush()

    def add_incident_link(self, row: IncidentAlertLinkRow) -> None:
        self._session.add(row)
        self._session.flush()

    def flush(self) -> None:
        self._session.flush()
