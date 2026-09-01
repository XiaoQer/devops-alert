from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any, Literal, cast

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from incident_intelligence.domain.incidents import (
    Incident,
    IncidentActivity,
    IncidentAlertLink,
)
from incident_intelligence.persistence.models import (
    FeishuEventReceiptRow,
    IncidentEvaluationJobRow,
    IncidentFeishuThreadRow,
    IncidentNotificationOutboxRow,
    IncidentNotificationRouteRow,
    OperationalIncidentActivityRow,
    OperationalIncidentAlertRow,
    OperationalIncidentRow,
)

AsyncJobState = Literal["PENDING", "LEASED", "SUCCEEDED", "FAILED"]
NotificationKind = Literal["CREATE_CARD", "UPDATE_CARD", "THREAD_REPLY"]


@dataclass(frozen=True, slots=True)
class IncidentEvaluationJobRecord:
    id: str
    alert_id: str
    alert_version: int
    state: AsyncJobState
    attempt_count: int
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    last_error_code: str | None
    outcome: str | None
    reason_codes: tuple[str, ...]
    incident_ids: tuple[str, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class IncidentNotificationRecord:
    id: str
    incident_id: str
    activity_id: str
    notification_key: str
    kind: NotificationKind
    state: AsyncJobState
    payload: dict[str, object]
    attempt_count: int
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    last_error_code: str | None
    feishu_message_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class IncidentNotificationRouteRecord:
    id: str
    environment: str
    chat_id: str
    chat_name: str
    enabled: bool
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class IncidentFeishuThreadRecord:
    id: str
    incident_id: str
    route_id: str
    chat_id: str
    root_message_id: str
    last_synced_at: datetime
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class FeishuEventReceiptRecord:
    id: str
    event_id: str
    event_type: str
    outcome: str
    received_at: datetime


class IncidentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, incident: Incident) -> None:
        self._session.add(OperationalIncidentRow(**_incident_values(incident)))
        self._session.flush()

    def get(self, incident_id: str, *, for_update: bool = False) -> Incident | None:
        statement = select(OperationalIncidentRow).where(
            OperationalIncidentRow.id == incident_id
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.scalar(statement)
        return None if row is None else _to_incident(row)

    def find_unresolved(
        self,
        *,
        rule_id: str,
        environment: str,
        group_key: str,
        for_update: bool = False,
    ) -> Incident | None:
        statement = select(OperationalIncidentRow).where(
            OperationalIncidentRow.incident_rule_id == rule_id,
            OperationalIncidentRow.environment == environment,
            OperationalIncidentRow.group_key == group_key,
            OperationalIncidentRow.open_boundary_key.is_not(None),
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.scalar(statement)
        return None if row is None else _to_incident(row)

    def update(self, incident: Incident, *, expected_version: int) -> bool:
        values = _incident_values(incident)
        values.pop("id")
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(OperationalIncidentRow)
                .where(
                    OperationalIncidentRow.id == incident.id,
                    OperationalIncidentRow.version == expected_version,
                )
                .values(**values)
            ),
        )
        self._session.flush()
        return result.rowcount == 1

    def link_alerts(self, links: tuple[IncidentAlertLink, ...]) -> tuple[str, ...]:
        inserted: list[str] = []
        for link in links:
            result = cast(
                CursorResult[Any],
                self._session.execute(
                    mysql_insert(OperationalIncidentAlertRow)
                    .values(
                        incident_id=link.incident_id,
                        alert_id=link.alert_id,
                        incident_rule_version=link.incident_rule_version,
                        first_trigger_window=link.first_trigger_window,
                        linked_at=link.linked_at,
                    )
                    .prefix_with("IGNORE")
                ),
            )
            if result.rowcount == 1:
                inserted.append(link.alert_id)
        self._session.flush()
        return tuple(inserted)

    def list_alert_ids(self, incident_id: str) -> tuple[str, ...]:
        rows = self._session.scalars(
            select(OperationalIncidentAlertRow.alert_id)
            .where(OperationalIncidentAlertRow.incident_id == incident_id)
            .order_by(
                OperationalIncidentAlertRow.linked_at,
                OperationalIncidentAlertRow.alert_id,
            )
        )
        return tuple(rows)

    def append_activities(self, activities: tuple[IncidentActivity, ...]) -> None:
        for activity in activities:
            self._session.execute(
                mysql_insert(OperationalIncidentActivityRow)
                .values(
                    id=activity.id,
                    incident_id=activity.incident_id,
                    kind=activity.kind,
                    occurred_at=activity.occurred_at,
                    actor_type=activity.actor_type,
                    actor=activity.actor,
                    summary=activity.summary,
                    activity_metadata=activity.metadata,
                )
                .prefix_with("IGNORE")
            )
        self._session.flush()

    def list_activities(self, incident_id: str) -> tuple[IncidentActivity, ...]:
        rows = self._session.scalars(
            select(OperationalIncidentActivityRow)
            .where(OperationalIncidentActivityRow.incident_id == incident_id)
            .order_by(
                OperationalIncidentActivityRow.occurred_at,
                OperationalIncidentActivityRow.id,
            )
        )
        return tuple(
            IncidentActivity.model_validate(
                {
                    "id": row.id,
                    "incident_id": row.incident_id,
                    "kind": row.kind,
                    "occurred_at": row.occurred_at,
                    "actor_type": row.actor_type,
                    "actor": row.actor,
                    "summary": row.summary,
                    "metadata": row.activity_metadata,
                }
            )
            for row in rows
        )


class IncidentEvaluationJobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(self, record: IncidentEvaluationJobRecord) -> bool:
        values = _job_values(record)
        result = cast(
            CursorResult[Any],
            self._session.execute(
                mysql_insert(IncidentEvaluationJobRow).values(**values).prefix_with("IGNORE")
            ),
        )
        self._session.flush()
        return result.rowcount == 1

    def lease_due(
        self,
        *,
        owner: str,
        now: datetime,
        lease_until: datetime,
        limit: int,
    ) -> tuple[IncidentEvaluationJobRecord, ...]:
        rows = tuple(
            self._session.scalars(
                select(IncidentEvaluationJobRow)
                .where(
                    or_(
                        (
                            (IncidentEvaluationJobRow.state == "PENDING")
                            & (IncidentEvaluationJobRow.available_at <= now)
                        ),
                        (
                            (IncidentEvaluationJobRow.state == "LEASED")
                            & (IncidentEvaluationJobRow.lease_expires_at <= now)
                        ),
                    )
                )
                .order_by(
                    IncidentEvaluationJobRow.available_at,
                    IncidentEvaluationJobRow.created_at,
                    IncidentEvaluationJobRow.id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for row in rows:
            row.state = "LEASED"
            row.attempt_count += 1
            row.lease_owner = owner
            row.lease_expires_at = lease_until
            row.updated_at = now
        self._session.flush()
        return tuple(_to_job(row) for row in rows)


class IncidentNotificationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(self, record: IncidentNotificationRecord) -> bool:
        values = asdict(record)
        result = cast(
            CursorResult[Any],
            self._session.execute(
                mysql_insert(IncidentNotificationOutboxRow)
                .values(**values)
                .prefix_with("IGNORE")
            ),
        )
        self._session.flush()
        return result.rowcount == 1

    def get(self, notification_id: str) -> IncidentNotificationRecord | None:
        row = self._session.get(IncidentNotificationOutboxRow, notification_id)
        return None if row is None else _to_notification(row)


class IncidentNotificationRouteRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, record: IncidentNotificationRouteRecord) -> None:
        self._session.add(
            IncidentNotificationRouteRow(
                **asdict(record),
                enabled_environment_key=record.environment if record.enabled else None,
            )
        )
        self._session.flush()

    def find_enabled(self, environment: str) -> IncidentNotificationRouteRecord | None:
        row = self._session.scalar(
            select(IncidentNotificationRouteRow).where(
                IncidentNotificationRouteRow.enabled_environment_key == environment
            )
        )
        return None if row is None else _to_route(row)


class IncidentFeishuThreadRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, record: IncidentFeishuThreadRecord) -> None:
        self._session.add(IncidentFeishuThreadRow(**asdict(record)))
        self._session.flush()

    def get_by_incident(self, incident_id: str) -> IncidentFeishuThreadRecord | None:
        row = self._session.scalar(
            select(IncidentFeishuThreadRow).where(
                IncidentFeishuThreadRow.incident_id == incident_id
            )
        )
        return None if row is None else _to_thread(row)

    def find_by_message(
        self,
        chat_id: str,
        root_message_id: str,
    ) -> IncidentFeishuThreadRecord | None:
        row = self._session.scalar(
            select(IncidentFeishuThreadRow).where(
                IncidentFeishuThreadRow.chat_id == chat_id,
                IncidentFeishuThreadRow.root_message_id == root_message_id,
            )
        )
        return None if row is None else _to_thread(row)


class FeishuEventReceiptRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record_once(self, record: FeishuEventReceiptRecord) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                mysql_insert(FeishuEventReceiptRow)
                .values(**asdict(record))
                .prefix_with("IGNORE")
            ),
        )
        self._session.flush()
        return result.rowcount == 1


def _incident_values(incident: Incident) -> dict[str, object]:
    return {
        **incident.model_dump(mode="python"),
        "open_boundary_key": (
            _open_boundary_key(incident) if incident.state != "RESOLVED" else None
        ),
    }


def _open_boundary_key(incident: Incident) -> str:
    value = "\x1f".join(
        (incident.incident_rule_id, incident.environment, incident.group_key)
    )
    return sha256(value.encode()).hexdigest()


def _to_incident(row: OperationalIncidentRow) -> Incident:
    return Incident.model_validate(
        {
            column.name: getattr(row, column.name)
            for column in OperationalIncidentRow.__table__.columns
            if column.name != "open_boundary_key"
        }
    )


def _job_values(record: IncidentEvaluationJobRecord) -> dict[str, object]:
    values = asdict(record)
    values["reason_codes"] = list(record.reason_codes)
    values["incident_ids"] = list(record.incident_ids)
    return values


def _to_job(row: IncidentEvaluationJobRow) -> IncidentEvaluationJobRecord:
    return IncidentEvaluationJobRecord(
        id=row.id,
        alert_id=row.alert_id,
        alert_version=row.alert_version,
        state=cast(AsyncJobState, row.state),
        attempt_count=row.attempt_count,
        available_at=row.available_at,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        last_error_code=row.last_error_code,
        outcome=row.outcome,
        reason_codes=tuple(row.reason_codes),
        incident_ids=tuple(row.incident_ids),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_notification(row: IncidentNotificationOutboxRow) -> IncidentNotificationRecord:
    return IncidentNotificationRecord(
        id=row.id,
        incident_id=row.incident_id,
        activity_id=row.activity_id,
        notification_key=row.notification_key,
        kind=cast(NotificationKind, row.kind),
        state=cast(AsyncJobState, row.state),
        payload=row.payload,
        attempt_count=row.attempt_count,
        available_at=row.available_at,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        last_error_code=row.last_error_code,
        feishu_message_id=row.feishu_message_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_route(row: IncidentNotificationRouteRow) -> IncidentNotificationRouteRecord:
    return IncidentNotificationRouteRecord(
        id=row.id,
        environment=row.environment,
        chat_id=row.chat_id,
        chat_name=row.chat_name,
        enabled=row.enabled,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_thread(row: IncidentFeishuThreadRow) -> IncidentFeishuThreadRecord:
    return IncidentFeishuThreadRecord(
        id=row.id,
        incident_id=row.incident_id,
        route_id=row.route_id,
        chat_id=row.chat_id,
        root_message_id=row.root_message_id,
        last_synced_at=row.last_synced_at,
        last_error_code=row.last_error_code,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
