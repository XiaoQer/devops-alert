from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from incident_intelligence.domain.diagnosis import DiagnosisRun, DiagnosisSnapshot
from incident_intelligence.persistence.models import (
    IncidentDiagnosisOperationRow,
    IncidentDiagnosisRunRow,
    IncidentDiagnosisSnapshotRow,
    IncidentDiagnosisTaskRow,
)

DiagnosisTaskState = Literal["PENDING", "LEASED", "SUCCEEDED", "FAILED"]


@dataclass(frozen=True, slots=True)
class DiagnosisTaskRecord:
    id: str
    diagnosis_run_id: str
    state: DiagnosisTaskState
    attempt_count: int
    next_attempt_at: datetime
    lease_owner: str | None
    lease_until: datetime | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DiagnosisOperationRecord:
    id: str
    scope: str
    idempotency_key_hash: str
    diagnosis_run_id: str
    created_at: datetime


class DiagnosisRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert_run_with_snapshot_and_task(
        self,
        run: DiagnosisRun,
        snapshot: DiagnosisSnapshot,
        task: DiagnosisTaskRecord,
    ) -> None:
        if snapshot.diagnosis_run_id != run.id or task.diagnosis_run_id != run.id:
            raise ValueError("diagnosis_run_snapshot_or_task_mismatch")
        self._session.add(_run_row(run))
        self._session.add(_snapshot_row(snapshot))
        self._session.add(_task_row(task))
        self._session.flush()

    def get(self, run_id: str, *, for_update: bool = False) -> DiagnosisRun | None:
        statement = select(IncidentDiagnosisRunRow).where(IncidentDiagnosisRunRow.id == run_id)
        if for_update:
            statement = statement.with_for_update()
        row = self._session.scalar(statement)
        return None if row is None else _run_domain(row)

    def get_snapshot(self, run_id: str) -> DiagnosisSnapshot | None:
        row = self._session.scalar(
            select(IncidentDiagnosisSnapshotRow).where(
                IncidentDiagnosisSnapshotRow.diagnosis_run_id == run_id
            )
        )
        return None if row is None else _snapshot_domain(row)

    def get_task(self, task_id: str) -> DiagnosisTaskRecord | None:
        row = self._session.get(IncidentDiagnosisTaskRow, task_id)
        return None if row is None else _task_record(row)

    def find_active(
        self,
        incident_id: str,
        *,
        for_update: bool = False,
    ) -> DiagnosisRun | None:
        statement = (
            select(IncidentDiagnosisRunRow)
            .where(IncidentDiagnosisRunRow.incident_id == incident_id)
            .where(IncidentDiagnosisRunRow.active_slot == 1)
            .order_by(IncidentDiagnosisRunRow.created_at.desc(), IncidentDiagnosisRunRow.id.desc())
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.scalar(statement)
        return None if row is None else _run_domain(row)

    def list_for_incident(
        self,
        incident_id: str,
        *,
        limit: int,
        offset: int,
    ) -> tuple[tuple[DiagnosisRun, ...], int]:
        rows = self._session.scalars(
            select(IncidentDiagnosisRunRow)
            .where(IncidentDiagnosisRunRow.incident_id == incident_id)
            .order_by(IncidentDiagnosisRunRow.created_at.desc(), IncidentDiagnosisRunRow.id.desc())
            .limit(limit)
            .offset(offset)
        )
        total = self._session.scalar(
            select(func.count())
            .select_from(IncidentDiagnosisRunRow)
            .where(IncidentDiagnosisRunRow.incident_id == incident_id)
        )
        return tuple(_run_domain(row) for row in rows), int(total or 0)


class DiagnosisTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, task_id: str) -> DiagnosisTaskRecord | None:
        row = self._session.get(IncidentDiagnosisTaskRow, task_id)
        return None if row is None else _task_record(row)

    def list_due(self, *, now: datetime, limit: int) -> tuple[str, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("诊断任务批次大小必须为 1 到 100")
        rows = self._session.scalars(
            select(IncidentDiagnosisTaskRow.id)
            .where(IncidentDiagnosisTaskRow.state == "PENDING")
            .where(IncidentDiagnosisTaskRow.next_attempt_at <= now)
            .where(IncidentDiagnosisTaskRow.attempt_count < 3)
            .order_by(
                IncidentDiagnosisTaskRow.next_attempt_at,
                IncidentDiagnosisTaskRow.created_at,
                IncidentDiagnosisTaskRow.id,
            )
            .limit(limit)
        )
        return tuple(rows)

    def requeue_expired(self, *, now: datetime) -> int:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(IncidentDiagnosisTaskRow)
                .where(IncidentDiagnosisTaskRow.state == "LEASED")
                .where(IncidentDiagnosisTaskRow.lease_until < now)
                .where(IncidentDiagnosisTaskRow.attempt_count < 3)
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
            update(IncidentDiagnosisTaskRow)
            .where(IncidentDiagnosisTaskRow.state == "LEASED")
            .where(IncidentDiagnosisTaskRow.lease_until < now)
            .where(IncidentDiagnosisTaskRow.attempt_count >= 3)
            .values(
                state="FAILED",
                lease_owner=None,
                lease_until=None,
                last_error_code="worker_lease_expired",
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
                update(IncidentDiagnosisTaskRow)
                .where(IncidentDiagnosisTaskRow.id == task_id)
                .where(IncidentDiagnosisTaskRow.state == "PENDING")
                .where(IncidentDiagnosisTaskRow.next_attempt_at <= now)
                .where(IncidentDiagnosisTaskRow.attempt_count < 3)
                .values(
                    state="LEASED",
                    attempt_count=IncidentDiagnosisTaskRow.attempt_count + 1,
                    lease_owner=owner,
                    lease_until=lease_until,
                    updated_at=now,
                )
            ),
        )
        self._session.flush()
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
        return self._finish(task_id, owner=owner, state="FAILED", error_code=error_code, now=now)

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
                update(IncidentDiagnosisTaskRow)
                .where(IncidentDiagnosisTaskRow.id == task_id)
                .where(IncidentDiagnosisTaskRow.state == "LEASED")
                .where(IncidentDiagnosisTaskRow.lease_owner == owner)
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
        self._session.flush()
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
                update(IncidentDiagnosisTaskRow)
                .where(IncidentDiagnosisTaskRow.id == task_id)
                .where(IncidentDiagnosisTaskRow.state == "LEASED")
                .where(IncidentDiagnosisTaskRow.lease_owner == owner)
                .values(
                    state=state,
                    lease_owner=None,
                    lease_until=None,
                    last_error_code=error_code,
                    updated_at=now,
                )
            ),
        )
        self._session.flush()
        return result.rowcount == 1


class DiagnosisOperationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, operation: DiagnosisOperationRecord) -> None:
        self._session.add(IncidentDiagnosisOperationRow(**asdict(operation)))
        self._session.flush()

    def find(
        self,
        *,
        scope: str,
        idempotency_key_hash: str,
    ) -> DiagnosisOperationRecord | None:
        row = self._session.scalar(
            select(IncidentDiagnosisOperationRow)
            .where(IncidentDiagnosisOperationRow.scope == scope)
            .where(IncidentDiagnosisOperationRow.idempotency_key_hash == idempotency_key_hash)
        )
        return None if row is None else _operation_record(row)


def _run_row(run: DiagnosisRun) -> IncidentDiagnosisRunRow:
    return IncidentDiagnosisRunRow(
        id=run.id,
        incident_id=run.incident_id,
        evidence_run_id=run.evidence_run_id,
        state=run.state,
        active_slot=1 if run.state in {"QUEUED", "RUNNING"} else None,
        requested_by=run.requested_by,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        version=run.version,
    )


def _run_domain(row: IncidentDiagnosisRunRow) -> DiagnosisRun:
    return DiagnosisRun.model_validate(
        {
            "id": row.id,
            "incident_id": row.incident_id,
            "evidence_run_id": row.evidence_run_id,
            "state": row.state,
            "requested_by": row.requested_by,
            "created_at": row.created_at,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
            "version": row.version,
        }
    )


def _snapshot_row(snapshot: DiagnosisSnapshot) -> IncidentDiagnosisSnapshotRow:
    return IncidentDiagnosisSnapshotRow(
        id=f"dsp_{snapshot.diagnosis_run_id[5:]}",
        diagnosis_run_id=snapshot.diagnosis_run_id,
        incident_snapshot={
            "incident_id": snapshot.incident_id,
            "evidence_run_id": snapshot.evidence_run_id,
            "alert_facts": [fact.model_dump(mode="json") for fact in snapshot.alert_facts],
        },
        evidence_snapshot={
            "references": [
                reference.model_dump(mode="json") for reference in snapshot.evidence_references
            ],
        },
        scope_snapshot={
            "environment": snapshot.environment,
            "service_name": snapshot.service_name,
            "alert_names": list(snapshot.alert_names),
            "knowledge_references": [
                reference.model_dump(mode="json") for reference in snapshot.knowledge_references
            ],
        },
        created_at=snapshot.created_at,
    )


def _snapshot_domain(row: IncidentDiagnosisSnapshotRow) -> DiagnosisSnapshot:
    incident_snapshot = dict(row.incident_snapshot)
    evidence_snapshot = dict(row.evidence_snapshot)
    scope_snapshot = dict(row.scope_snapshot)
    return DiagnosisSnapshot.model_validate(
        {
            "diagnosis_run_id": row.diagnosis_run_id,
            "incident_id": incident_snapshot["incident_id"],
            "evidence_run_id": incident_snapshot["evidence_run_id"],
            "environment": scope_snapshot["environment"],
            "service_name": scope_snapshot["service_name"],
            "alert_names": tuple(_snapshot_list(scope_snapshot, "alert_names")),
            "alert_facts": tuple(_snapshot_list(incident_snapshot, "alert_facts")),
            "evidence_references": tuple(_snapshot_list(evidence_snapshot, "references")),
            "knowledge_references": tuple(_snapshot_list(scope_snapshot, "knowledge_references")),
            "created_at": row.created_at,
        }
    )


def _snapshot_list(snapshot: dict[str, object], key: str) -> list[object]:
    value = snapshot.get(key)
    if not isinstance(value, list):
        raise ValueError("diagnosis_snapshot_invalid")
    return value


def _task_row(task: DiagnosisTaskRecord) -> IncidentDiagnosisTaskRow:
    return IncidentDiagnosisTaskRow(**asdict(task))


def _task_record(row: IncidentDiagnosisTaskRow) -> DiagnosisTaskRecord:
    return DiagnosisTaskRecord(
        id=row.id,
        diagnosis_run_id=row.diagnosis_run_id,
        state=cast(DiagnosisTaskState, row.state),
        attempt_count=row.attempt_count,
        next_attempt_at=row.next_attempt_at,
        lease_owner=row.lease_owner,
        lease_until=row.lease_until,
        last_error_code=row.last_error_code,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _operation_record(row: IncidentDiagnosisOperationRow) -> DiagnosisOperationRecord:
    return DiagnosisOperationRecord(
        id=row.id,
        scope=row.scope,
        idempotency_key_hash=row.idempotency_key_hash,
        diagnosis_run_id=row.diagnosis_run_id,
        created_at=row.created_at,
    )
