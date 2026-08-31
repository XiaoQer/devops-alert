from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from incident_intelligence.domain.incident_rule_evaluation import AlertEvaluationFact
from incident_intelligence.persistence.models import AlertLifecycleRow, AlertSourceRow


@dataclass(frozen=True, slots=True)
class AlertRecord:
    alert: AlertLifecycleRow
    source: AlertSourceRow


@dataclass(frozen=True, slots=True)
class AlertTrendCount:
    source_id: str
    source_name: str
    source_type: str
    management_type: str
    bucket_epoch: int
    count: int


class AlertRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_alerts(
        self,
        *,
        alert_source_id: str | None,
        state: str | None,
        severity: str | None,
        environment: str | None,
        service: str | None,
        observed_from: datetime | None,
        observed_to: datetime | None,
        received_from: datetime | None,
        received_to: datetime | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> tuple[AlertRecord, ...]:
        statement = select(AlertLifecycleRow, AlertSourceRow).join(
            AlertSourceRow, AlertSourceRow.id == AlertLifecycleRow.alert_source_id
        )
        statement = _apply_filters(
            statement,
            alert_source_id=alert_source_id,
            state=state,
            severity=severity,
            environment=environment,
            service=service,
            observed_from=observed_from,
            observed_to=observed_to,
            received_from=received_from,
            received_to=received_to,
            query=query,
        )
        rows = self._session.execute(
            statement.order_by(
                AlertLifecycleRow.first_received_at.desc(),
                AlertLifecycleRow.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return tuple(AlertRecord(alert=row[0], source=row[1]) for row in rows)

    def count_alerts(self, **filters: Any) -> int:
        statement = _apply_filters(
            select(func.count()).select_from(AlertLifecycleRow),
            **filters,
        )
        return self._session.scalar(statement) or 0

    def find_alert(self, alert_id: str) -> AlertRecord | None:
        row = self._session.execute(
            select(AlertLifecycleRow, AlertSourceRow)
            .join(AlertSourceRow, AlertSourceRow.id == AlertLifecycleRow.alert_source_id)
            .where(AlertLifecycleRow.id == alert_id)
        ).one_or_none()
        return None if row is None else AlertRecord(alert=row[0], source=row[1])

    def count_by_source_and_first_received_bucket(
        self,
        *,
        received_from: datetime,
        received_to: datetime,
        bucket_seconds: int,
    ) -> tuple[AlertTrendCount, ...]:
        bucket_epoch = (
            func.floor(func.unix_timestamp(AlertLifecycleRow.first_received_at) / bucket_seconds)
            * bucket_seconds
        ).label("bucket_epoch")
        rows = self._session.execute(
            select(
                AlertSourceRow.id,
                AlertSourceRow.name,
                AlertSourceRow.source_type,
                AlertSourceRow.management_type,
                bucket_epoch,
                func.count().label("alert_count"),
            )
            .join(AlertSourceRow, AlertSourceRow.id == AlertLifecycleRow.alert_source_id)
            .where(
                AlertLifecycleRow.first_received_at >= received_from,
                AlertLifecycleRow.first_received_at < received_to,
            )
            .group_by(
                AlertSourceRow.id,
                AlertSourceRow.name,
                AlertSourceRow.source_type,
                AlertSourceRow.management_type,
                bucket_epoch,
            )
            .order_by(AlertSourceRow.name, AlertSourceRow.id, bucket_epoch)
        )
        return tuple(
            AlertTrendCount(
                source_id=row[0],
                source_name=row[1],
                source_type=row[2],
                management_type=row[3],
                bucket_epoch=int(row[4]),
                count=int(row[5]),
            )
            for row in rows
        )

    def list_for_rule_evaluation(
        self,
        *,
        environment: str,
        alert_source_ids: tuple[str, ...],
        services: tuple[str, ...],
        received_from: datetime,
        received_to: datetime,
        limit: int,
    ) -> tuple[AlertEvaluationFact, ...]:
        statement = select(AlertLifecycleRow).where(
            AlertLifecycleRow.environment == environment,
            AlertLifecycleRow.first_received_at >= received_from,
            AlertLifecycleRow.first_received_at < received_to,
        )
        if alert_source_ids:
            statement = statement.where(AlertLifecycleRow.alert_source_id.in_(alert_source_ids))
        if services:
            statement = statement.where(AlertLifecycleRow.service.in_(services))
        rows = self._session.scalars(
            statement.order_by(
                AlertLifecycleRow.first_received_at,
                AlertLifecycleRow.id,
            ).limit(limit)
        )
        return tuple(
            AlertEvaluationFact.model_validate(
                {
                    "id": row.id,
                    "alert_source_id": row.alert_source_id,
                    "alert_name": row.alert_name,
                    "state": row.state,
                    "severity": row.severity,
                    "environment": row.environment,
                    "service": row.service,
                    "entity_key": row.entity_key,
                    "entity_display_name": row.entity_display_name,
                    "first_received_at": row.first_received_at,
                }
            )
            for row in rows
        )


def _apply_filters(
    statement: Any,
    *,
    alert_source_id: str | None,
    state: str | None,
    severity: str | None,
    environment: str | None,
    service: str | None,
    observed_from: datetime | None,
    observed_to: datetime | None,
    received_from: datetime | None,
    received_to: datetime | None,
    query: str | None,
) -> Any:
    if alert_source_id is not None:
        statement = statement.where(AlertLifecycleRow.alert_source_id == alert_source_id)
    if state is not None:
        statement = statement.where(AlertLifecycleRow.state == state)
    if severity is not None:
        statement = statement.where(AlertLifecycleRow.severity == severity)
    if environment is not None:
        statement = statement.where(AlertLifecycleRow.environment == environment)
    if service is not None:
        statement = statement.where(AlertLifecycleRow.service == service)
    if observed_from is not None:
        statement = statement.where(AlertLifecycleRow.first_observed_at >= observed_from)
    if observed_to is not None:
        statement = statement.where(AlertLifecycleRow.first_observed_at <= observed_to)
    if received_from is not None:
        statement = statement.where(AlertLifecycleRow.first_received_at >= received_from)
    if received_to is not None:
        statement = statement.where(AlertLifecycleRow.first_received_at <= received_to)
    if query is not None:
        pattern = f"%{query}%"
        statement = statement.where(
            or_(
                AlertLifecycleRow.alert_name.like(pattern),
                AlertLifecycleRow.summary.like(pattern),
                AlertLifecycleRow.description.like(pattern),
                AlertLifecycleRow.service.like(pattern),
                AlertLifecycleRow.entity_display_name.like(pattern),
            )
        )
    return statement
