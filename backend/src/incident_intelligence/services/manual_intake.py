from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.exc import IntegrityError

from incident_intelligence.domain.enums import AlertState, DiagnosisState, IncidentState
from incident_intelligence.domain.forbidden_identity import reject_forbidden_identity
from incident_intelligence.domain.models import (
    Alert,
    DiagnosisRun,
    Environment,
    FactKey,
    FactValue,
    Incident,
    ServiceName,
    Severity,
    SignalEvent,
    Summary,
    Title,
    UtcAwareDatetime,
)
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.models import IngestionKeyRow
from incident_intelligence.persistence.repositories import RecordRepositories
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork


class ManualIntakeCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: Title
    summary: Summary
    severity: Severity
    service: ServiceName
    environment: Environment
    observed_at: UtcAwareDatetime
    facts: dict[FactKey, FactValue] = Field(default_factory=dict, max_length=50)


class ManualIntakeResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_event_id: str
    alert_id: str
    incident_id: str
    diagnosis_run_id: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class IdempotencyConflict(Exception):
    reason_code: str = "idempotency_conflict"

    def __str__(self) -> str:
        return self.reason_code


IdempotencyKey = Annotated[str, StringConstraints(min_length=1, max_length=256)]


class ManualIntakeService:
    SCOPE = "manual-report"
    SOURCE_INSTANCE = sha256(b"manual").hexdigest()

    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[IdPrefix], str] = new_id,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory

    def submit(
        self,
        command: ManualIntakeCommand,
        idempotency_key: IdempotencyKey,
        actor: str,
        request_id: str,
    ) -> ManualIntakeResult:
        reject_forbidden_identity(command.model_dump(mode="json"))
        fingerprint = _fingerprint(command)

        try:
            return self._submit_once(command, idempotency_key, actor, request_id, fingerprint)
        except IntegrityError as original_error:
            replay = self._replay_after_conflict(idempotency_key, fingerprint)
            if replay is None:
                raise original_error
            return replay

    def _submit_once(
        self,
        command: ManualIntakeCommand,
        idempotency_key: str,
        actor: str,
        request_id: str,
        fingerprint: str,
    ) -> ManualIntakeResult:
        with self._uow_factory() as uow:
            records = _records(uow)
            existing = records.find_ingestion(self.SCOPE, idempotency_key)
            if existing is not None:
                return _result_from_existing(existing, fingerprint)

            now = self._clock().astimezone(UTC)
            signal = SignalEvent(
                id=self._id_factory("sig"),
                source="manual",
                source_event_id=idempotency_key,
                event_type="manual.reported",
                title=command.title,
                summary=command.summary,
                severity=command.severity,
                service=command.service,
                environment=command.environment,
                observed_at=command.observed_at,
                received_at=now,
                facts=command.facts,
                payload_fingerprint=fingerprint,
                created_at=now,
            )
            alert = Alert(
                id=self._id_factory("alt"),
                signal_event_id=signal.id,
                source="manual",
                source_instance=self.SOURCE_INSTANCE,
                source_alert_key=sha256(idempotency_key.encode("utf-8")).hexdigest(),
                state=AlertState.ACTIVE,
                title=signal.title,
                severity=signal.severity,
                service=signal.service,
                environment=signal.environment,
                first_observed_at=signal.observed_at,
                last_observed_at=signal.observed_at,
                state_changed_at=now,
                created_at=now,
            )
            incident = Incident(
                id=self._id_factory("inc"),
                primary_alert_id=alert.id,
                state=IncidentState.DETECTED,
                title=alert.title,
                severity=alert.severity,
                service=alert.service,
                environment=alert.environment,
                detected_at=now,
                created_at=now,
            )
            diagnosis = DiagnosisRun(
                id=self._id_factory("diag"),
                incident_id=incident.id,
                incident_context_version=incident.version,
                state=DiagnosisState.QUEUED,
                created_at=now,
            )
            result = ManualIntakeResult(
                signal_event_id=signal.id,
                alert_id=alert.id,
                incident_id=incident.id,
                diagnosis_run_id=diagnosis.id,
                replayed=False,
            )
            records.add_ingestion(
                scope=self.SCOPE,
                idempotency_key=idempotency_key,
                payload_fingerprint=fingerprint,
                signal_event_id=result.signal_event_id,
                alert_id=result.alert_id,
                incident_id=result.incident_id,
                diagnosis_run_id=result.diagnosis_run_id,
                created_at=now,
            )
            records.add_signal(signal)
            records.add_alert(alert)
            records.add_incident(incident)
            records.add_diagnosis(diagnosis)
            self._append_audits(records, result, actor, request_id, now)
            uow.commit()
            return result

    def _replay_after_conflict(
        self, idempotency_key: str, fingerprint: str
    ) -> ManualIntakeResult | None:
        with self._uow_factory() as uow:
            existing = _records(uow).find_ingestion(self.SCOPE, idempotency_key)
            if existing is None:
                return None
            return _result_from_existing(existing, fingerprint)

    def _append_audits(
        self,
        records: RecordRepositories,
        result: ManualIntakeResult,
        actor: str,
        request_id: str,
        created_at: datetime,
    ) -> None:
        audit_rows = (
            ("signal.received", "signal_event", result.signal_event_id, None),
            ("alert.opened", "alert", result.alert_id, result.signal_event_id),
            ("incident.detected", "incident", result.incident_id, result.alert_id),
            ("diagnosis.queued", "diagnosis_run", result.diagnosis_run_id, result.incident_id),
        )
        for action, resource_type, resource_id, parent_id in audit_rows:
            details = {"reason_code": "manual_report"}
            if parent_id is not None:
                details["parent_id"] = parent_id
            records.add_audit(
                audit_id=self._id_factory("aud"),
                actor=actor,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=request_id,
                details=details,
                created_at=created_at,
            )


def _fingerprint(command: ManualIntakeCommand) -> str:
    canonical = json.dumps(
        command.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _records(uow: SqlAlchemyUnitOfWork) -> RecordRepositories:
    if uow.records is None:
        raise RuntimeError("工作单元没有可用仓储")
    return uow.records


def _result_from_existing(existing: IngestionKeyRow, fingerprint: str) -> ManualIntakeResult:
    if existing.payload_fingerprint != fingerprint:
        raise IdempotencyConflict()
    return ManualIntakeResult(
        signal_event_id=existing.signal_event_id,
        alert_id=existing.alert_id,
        incident_id=existing.incident_id,
        diagnosis_run_id=existing.diagnosis_run_id,
        replayed=True,
    )
