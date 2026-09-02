from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from incident_intelligence.domain.evidence import (
    EvidenceContext,
    EvidenceItem,
    EvidenceRun,
    EvidenceWindow,
)
from incident_intelligence.persistence.models import (
    EvidenceCollectionOperationRow,
    EvidenceCollectionTaskRow,
    EvidenceItemRow,
    EvidenceRunRow,
    MonitoringDataSourceRow,
)

EvidenceTaskState = Literal["PENDING", "LEASED", "SUCCEEDED", "FAILED"]


@dataclass(frozen=True)
class MonitoringDataSourceRecord:
    id: str
    name: str
    environment: str
    source_type: str
    base_url: str
    credential_env_key: str | None
    field_mapping: dict[str, str]
    verify_tls: bool
    enabled: bool
    version: int
    last_test_state: str | None
    last_test_latency_ms: int | None
    last_compatible_version: str | None
    last_test_error_code: str | None
    last_tested_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class EvidenceTaskRecord:
    id: str
    evidence_run_id: str
    state: EvidenceTaskState
    attempt_count: int
    next_attempt_at: datetime
    lease_owner: str | None
    lease_until: datetime | None
    last_error_code: str | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class EvidenceOperationRecord:
    id: str
    scope: str
    idempotency_key_hash: str
    command_fingerprint: str
    evidence_run_id: str
    actor: str
    request_id: str
    completed_at: datetime


class MonitoringDataSourceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, source: MonitoringDataSourceRecord) -> None:
        self._session.add(_source_row(source))
        self._session.flush()

    def get(self, source_id: str, *, for_update: bool = False) -> MonitoringDataSourceRecord | None:
        statement = select(MonitoringDataSourceRow).where(MonitoringDataSourceRow.id == source_id)
        if for_update:
            statement = statement.with_for_update()
        row = self._session.scalar(statement)
        return None if row is None else _source_record(row)

    def list_all(self) -> tuple[MonitoringDataSourceRecord, ...]:
        rows = self._session.scalars(
            select(MonitoringDataSourceRow).order_by(
                MonitoringDataSourceRow.environment,
                MonitoringDataSourceRow.source_type,
                MonitoringDataSourceRow.name,
                MonitoringDataSourceRow.id,
            )
        )
        return tuple(_source_record(row) for row in rows)

    def update(
        self,
        source: MonitoringDataSourceRecord,
        *,
        expected_version: int,
    ) -> bool:
        values = source.__dict__.copy()
        values.pop("id")
        values["enabled_slot"] = 1 if source.enabled else None
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(MonitoringDataSourceRow)
                .where(MonitoringDataSourceRow.id == source.id)
                .where(MonitoringDataSourceRow.version == expected_version)
                .values(**values)
            ),
        )
        self._session.flush()
        return result.rowcount == 1

    def find_enabled(
        self,
        *,
        environment: str,
        source_type: str,
    ) -> MonitoringDataSourceRecord | None:
        row = self._session.scalar(
            select(MonitoringDataSourceRow)
            .where(MonitoringDataSourceRow.environment == environment)
            .where(MonitoringDataSourceRow.source_type == source_type)
            .where(MonitoringDataSourceRow.enabled.is_(True))
        )
        return None if row is None else _source_record(row)


class EvidenceRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, run: EvidenceRun) -> None:
        self._session.add(_run_row(run))
        self._session.flush()

    def insert_run_with_task(self, run: EvidenceRun, task: EvidenceTaskRecord) -> None:
        self.insert(run)
        self._session.add(_task_row(task))
        self._session.flush()

    def get(self, run_id: str, *, for_update: bool = False) -> EvidenceRun | None:
        statement = select(EvidenceRunRow).where(EvidenceRunRow.id == run_id)
        if for_update:
            statement = statement.with_for_update()
        row = self._session.scalar(statement)
        return None if row is None else _run_domain(row)

    def find_active(self, incident_id: str) -> EvidenceRun | None:
        row = self._session.scalar(
            select(EvidenceRunRow)
            .where(EvidenceRunRow.incident_id == incident_id)
            .where(EvidenceRunRow.state.in_(("QUEUED", "RUNNING")))
            .order_by(EvidenceRunRow.created_at.desc(), EvidenceRunRow.id.desc())
            .limit(1)
        )
        return None if row is None else _run_domain(row)

    def update(self, run: EvidenceRun, *, expected_version: int) -> bool:
        row = _run_row(run)
        values = {
            column.name: getattr(row, column.name)
            for column in EvidenceRunRow.__table__.columns
            if column.name not in {"id", "incident_id", "trigger_kind", "automatic_slot"}
        }
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(EvidenceRunRow)
                .where(EvidenceRunRow.id == run.id)
                .where(EvidenceRunRow.version == expected_version)
                .values(**values)
            ),
        )
        self._session.flush()
        return result.rowcount == 1

    def append_item(self, item: EvidenceItem) -> None:
        self._session.add(_item_row(item))
        self._session.flush()

    def list_items(self, run_id: str, *, limit: int = 101) -> tuple[EvidenceItem, ...]:
        rows = self._session.scalars(
            select(EvidenceItemRow)
            .where(EvidenceItemRow.evidence_run_id == run_id)
            .order_by(EvidenceItemRow.created_at, EvidenceItemRow.id)
            .limit(limit)
        )
        return tuple(_item_domain(row) for row in rows)


class EvidenceTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, task: EvidenceTaskRecord) -> None:
        self._session.add(_task_row(task))
        self._session.flush()

    def get(self, task_id: str) -> EvidenceTaskRecord | None:
        row = self._session.get(EvidenceCollectionTaskRow, task_id)
        return None if row is None else _task_record(row)

    def list_due(self, *, now: datetime, limit: int) -> tuple[str, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("取证任务批次大小必须为 1 到 100")
        rows = self._session.scalars(
            select(EvidenceCollectionTaskRow.id)
            .where(EvidenceCollectionTaskRow.state == "PENDING")
            .where(EvidenceCollectionTaskRow.next_attempt_at <= now)
            .where(EvidenceCollectionTaskRow.attempt_count < 5)
            .order_by(
                EvidenceCollectionTaskRow.next_attempt_at,
                EvidenceCollectionTaskRow.created_at,
                EvidenceCollectionTaskRow.id,
            )
            .limit(limit)
        )
        return tuple(rows)

    def requeue_expired(self, *, now: datetime) -> int:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(EvidenceCollectionTaskRow)
                .where(EvidenceCollectionTaskRow.state == "LEASED")
                .where(EvidenceCollectionTaskRow.lease_until < now)
                .where(EvidenceCollectionTaskRow.attempt_count < 5)
                .values(
                    state="PENDING",
                    next_attempt_at=now,
                    lease_owner=None,
                    lease_until=None,
                    last_error_code="worker_lease_expired",
                    updated_at=now,
                )
            ),
        )
        self._session.execute(
            update(EvidenceCollectionTaskRow)
            .where(EvidenceCollectionTaskRow.state == "LEASED")
            .where(EvidenceCollectionTaskRow.lease_until < now)
            .where(EvidenceCollectionTaskRow.attempt_count >= 5)
            .values(
                state="FAILED",
                lease_owner=None,
                lease_until=None,
                last_error_code="worker_lease_expired",
                completed_at=now,
                updated_at=now,
            )
        )
        self._session.flush()
        return result.rowcount

    def claim_due(
        self,
        task_id: str,
        *,
        owner: str,
        now: datetime,
        lease_until: datetime,
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(EvidenceCollectionTaskRow)
                .where(EvidenceCollectionTaskRow.id == task_id)
                .where(EvidenceCollectionTaskRow.state == "PENDING")
                .where(EvidenceCollectionTaskRow.next_attempt_at <= now)
                .where(EvidenceCollectionTaskRow.attempt_count < 5)
                .values(
                    state="LEASED",
                    attempt_count=EvidenceCollectionTaskRow.attempt_count + 1,
                    lease_owner=owner,
                    lease_until=lease_until,
                    updated_at=now,
                )
            ),
        )
        return result.rowcount == 1

    def complete(self, task_id: str, *, owner: str, now: datetime) -> bool:
        return self._finish(task_id, owner=owner, state="SUCCEEDED", now=now)

    def fail(
        self,
        task_id: str,
        *,
        owner: str,
        error_code: str,
        now: datetime,
    ) -> bool:
        return self._finish(
            task_id,
            owner=owner,
            state="FAILED",
            now=now,
            error_code=error_code,
        )

    def reschedule(
        self,
        task_id: str,
        *,
        owner: str,
        error_code: str,
        next_attempt_at: datetime,
        now: datetime,
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(EvidenceCollectionTaskRow)
                .where(EvidenceCollectionTaskRow.id == task_id)
                .where(EvidenceCollectionTaskRow.state == "LEASED")
                .where(EvidenceCollectionTaskRow.lease_owner == owner)
                .values(
                    state="PENDING",
                    next_attempt_at=next_attempt_at,
                    lease_owner=None,
                    lease_until=None,
                    last_error_code=error_code,
                    updated_at=now,
                )
            ),
        )
        return result.rowcount == 1

    def _finish(
        self,
        task_id: str,
        *,
        owner: str,
        state: Literal["SUCCEEDED", "FAILED"],
        now: datetime,
        error_code: str | None = None,
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(EvidenceCollectionTaskRow)
                .where(EvidenceCollectionTaskRow.id == task_id)
                .where(EvidenceCollectionTaskRow.state == "LEASED")
                .where(EvidenceCollectionTaskRow.lease_owner == owner)
                .values(
                    state=state,
                    lease_owner=None,
                    lease_until=None,
                    last_error_code=error_code,
                    completed_at=now,
                    updated_at=now,
                )
            ),
        )
        return result.rowcount == 1


class EvidenceOperationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, operation: EvidenceOperationRecord) -> None:
        self._session.add(EvidenceCollectionOperationRow(**operation.__dict__))
        self._session.flush()

    def find(self, *, scope: str, idempotency_key_hash: str) -> EvidenceOperationRecord | None:
        row = self._session.scalar(
            select(EvidenceCollectionOperationRow)
            .where(EvidenceCollectionOperationRow.scope == scope)
            .where(EvidenceCollectionOperationRow.idempotency_key_hash == idempotency_key_hash)
        )
        return (
            None
            if row is None
            else EvidenceOperationRecord(
                id=row.id,
                scope=row.scope,
                idempotency_key_hash=row.idempotency_key_hash,
                command_fingerprint=row.command_fingerprint,
                evidence_run_id=row.evidence_run_id,
                actor=row.actor,
                request_id=row.request_id,
                completed_at=row.completed_at,
            )
        )


def _run_row(run: EvidenceRun) -> EvidenceRunRow:
    return EvidenceRunRow(
        id=run.id,
        incident_id=run.incident_id,
        trigger_kind=run.trigger,
        automatic_slot=1 if run.trigger == "AUTOMATIC" else None,
        state=run.state,
        anchor_at=run.anchor_at,
        baseline_start=run.window.baseline_start,
        baseline_end=run.window.baseline_end,
        fault_start=run.window.fault_start,
        fault_end=run.window.fault_end,
        environment=run.context.environment,
        service_name=run.context.service_name,
        alert_names=list(run.context.alert_names),
        context_facts=dict(run.context.facts),
        package_versions=dict(run.package_versions),
        succeeded_count=run.succeeded_count,
        skipped_count=run.skipped_count,
        missing_count=run.missing_count,
        failed_count=run.failed_count,
        failure_summary=run.failure_summary,
        requested_by=run.requested_by,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        version=run.version,
    )


def _run_domain(row: EvidenceRunRow) -> EvidenceRun:
    return EvidenceRun.model_validate(
        {
            "id": row.id,
            "incident_id": row.incident_id,
            "trigger": row.trigger_kind,
            "state": row.state,
            "anchor_at": row.anchor_at,
            "window": EvidenceWindow(
                baseline_start=row.baseline_start,
                baseline_end=row.baseline_end,
                fault_start=row.fault_start,
                fault_end=row.fault_end,
            ),
            "context": EvidenceContext(
                environment=row.environment,
                service_name=row.service_name,
                alert_names=tuple(row.alert_names),
                facts=dict(row.context_facts),
            ),
            "package_versions": dict(row.package_versions),
            "succeeded_count": row.succeeded_count,
            "skipped_count": row.skipped_count,
            "missing_count": row.missing_count,
            "failed_count": row.failed_count,
            "failure_summary": row.failure_summary,
            "requested_by": row.requested_by,
            "created_at": row.created_at,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
            "version": row.version,
        }
    )


def _item_row(item: EvidenceItem) -> EvidenceItemRow:
    return EvidenceItemRow(**item.model_dump())


def _item_domain(row: EvidenceItemRow) -> EvidenceItem:
    return EvidenceItem.model_validate(
        {column.name: getattr(row, column.name) for column in EvidenceItemRow.__table__.columns}
    )


def _task_row(task: EvidenceTaskRecord) -> EvidenceCollectionTaskRow:
    return EvidenceCollectionTaskRow(**task.__dict__)


def _task_record(row: EvidenceCollectionTaskRow) -> EvidenceTaskRecord:
    return EvidenceTaskRecord(
        id=row.id,
        evidence_run_id=row.evidence_run_id,
        state=cast(EvidenceTaskState, row.state),
        attempt_count=row.attempt_count,
        next_attempt_at=row.next_attempt_at,
        lease_owner=row.lease_owner,
        lease_until=row.lease_until,
        last_error_code=row.last_error_code,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _source_row(source: MonitoringDataSourceRecord) -> MonitoringDataSourceRow:
    return MonitoringDataSourceRow(
        **source.__dict__,
        enabled_slot=1 if source.enabled else None,
    )


def _source_record(row: MonitoringDataSourceRow) -> MonitoringDataSourceRecord:
    return MonitoringDataSourceRecord(
        id=row.id,
        name=row.name,
        environment=row.environment,
        source_type=row.source_type,
        base_url=row.base_url,
        credential_env_key=row.credential_env_key,
        field_mapping=dict(row.field_mapping),
        verify_tls=row.verify_tls,
        enabled=row.enabled,
        version=row.version,
        last_test_state=row.last_test_state,
        last_test_latency_ms=row.last_test_latency_ms,
        last_compatible_version=row.last_compatible_version,
        last_test_error_code=row.last_test_error_code,
        last_tested_at=row.last_tested_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
