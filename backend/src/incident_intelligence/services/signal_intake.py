from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError

from incident_intelligence.domain.forbidden_identity import reject_forbidden_identity
from incident_intelligence.domain.models import Alert, SignalEvent
from incident_intelligence.domain.signal_intake import (
    ProjectionOutcome,
    SignalCommand,
    decide_alert_projection,
)
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.models import AlertRow, SignalIntakeResultRow
from incident_intelligence.persistence.repositories import RecordRepositories
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork


class SignalIntakeItemResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_event_id: str
    alert_id: str | None
    outcome: ProjectionOutcome
    replayed: bool


class SignalIntakeCounts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    opened: int = 0
    updated: int = 0
    resolved: int = 0
    reopened: int = 0
    stale: int = 0
    orphan_resolved: int = 0
    replayed: int = 0


class SignalIntakeBatchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[SignalIntakeItemResult, ...]
    counts: SignalIntakeCounts


@dataclass(frozen=True, slots=True)
class SourceEventConflict(Exception):
    reason_code: str = "source_event_conflict"

    def __str__(self) -> str:
        return self.reason_code


class SignalIntakeService:
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

    def submit_batch(
        self,
        commands: Sequence[SignalCommand],
        actor: str,
        request_id: str,
    ) -> SignalIntakeBatchResult:
        for command in commands:
            reject_forbidden_identity(command.model_dump(mode="json"))
        fingerprints = tuple(_fingerprint(command) for command in commands)
        try:
            return self._submit_once(commands, fingerprints, actor, request_id)
        except IntegrityError:
            return self._submit_once(commands, fingerprints, actor, request_id)

    def _submit_once(
        self,
        commands: Sequence[SignalCommand],
        fingerprints: tuple[str, ...],
        actor: str,
        request_id: str,
    ) -> SignalIntakeBatchResult:
        with self._uow_factory() as uow:
            records = _records(uow)
            items = tuple(
                self._submit_command(records, command, fingerprint, actor, request_id)
                for command, fingerprint in zip(commands, fingerprints, strict=True)
            )
            uow.commit()
        return SignalIntakeBatchResult(
            items=items,
            counts=_counts(items),
        )

    def _submit_command(
        self,
        records: RecordRepositories,
        command: SignalCommand,
        fingerprint: str,
        actor: str,
        request_id: str,
    ) -> SignalIntakeItemResult:
        existing = records.find_signal_result(command.source, command.source_event_id)
        if existing is not None:
            return _replay_result(existing, fingerprint)

        now = self._clock().astimezone(UTC)
        signal = SignalEvent(
            id=self._id_factory("sig"),
            source=command.source,
            source_event_id=command.source_event_id,
            event_type=command.event_type,
            title=command.title,
            summary=command.summary,
            severity=command.severity,
            service=command.service,
            environment=command.environment,
            observed_at=command.event_at,
            received_at=now,
            facts=command.facts,
            payload_fingerprint=fingerprint,
            created_at=now,
        )
        records.add_signal(signal)
        current = records.find_alert_for_update(
            command.source,
            command.source_instance,
            command.source_alert_key,
        )
        decision = decide_alert_projection(
            current=None if current is None else _alert_from_row(current),
            command=command,
            new_alert_id=self._id_factory("alt"),
            signal_event_id=signal.id,
            now=now,
        )
        if decision.changes_projection:
            if current is None and decision.alert is not None:
                records.add_alert(decision.alert)
            elif decision.alert is not None:
                records.update_alert(decision.alert)
        alert_id = None if decision.alert is None else decision.alert.id
        records.add_signal_result(
            SignalIntakeResultRow(
                source=command.source,
                source_event_id=command.source_event_id,
                command_fingerprint=fingerprint,
                signal_event_id=signal.id,
                alert_id=alert_id,
                outcome=decision.outcome,
                created_at=now,
            )
        )
        self._append_audit(
            records,
            audit_id=self._id_factory("aud"),
            actor=actor,
            action="signal.received",
            resource_type="signal_event",
            resource_id=signal.id,
            request_id=request_id,
            reason_code="external_signal_received",
            adapter=command.source,
            created_at=now,
        )
        self._append_audit(
            records,
            audit_id=self._id_factory("aud"),
            actor=actor,
            action=_audit_action(decision.outcome),
            resource_type="alert" if alert_id is not None else "signal_event",
            resource_id=alert_id or signal.id,
            request_id=request_id,
            reason_code=decision.reason_code,
            adapter=command.source,
            parent_id=signal.id,
            created_at=now,
        )
        return SignalIntakeItemResult(
            signal_event_id=signal.id,
            alert_id=alert_id,
            outcome=decision.outcome,
            replayed=False,
        )

    @staticmethod
    def _append_audit(
        records: RecordRepositories,
        *,
        audit_id: str,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        request_id: str,
        reason_code: str,
        adapter: str,
        created_at: datetime,
        parent_id: str | None = None,
    ) -> None:
        details = {"reason_code": reason_code, "adapter": adapter}
        if parent_id is not None:
            details["parent_id"] = parent_id
        records.add_audit(
            audit_id=audit_id,
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            details=details,
            created_at=created_at,
        )


def _fingerprint(command: SignalCommand) -> str:
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


def _alert_from_row(row: AlertRow) -> Alert:
    return Alert.model_validate(
        {
            "id": row.id,
            "signal_event_id": row.signal_event_id,
            "source": row.source,
            "source_instance": row.source_instance,
            "source_alert_key": row.source_alert_key,
            "state": row.state,
            "title": row.title,
            "severity": row.severity,
            "service": row.service,
            "environment": row.environment,
            "first_observed_at": row.first_observed_at,
            "last_observed_at": row.last_observed_at,
            "state_changed_at": row.state_changed_at,
            "created_at": row.created_at,
            "version": row.version,
        }
    )


def _replay_result(
    existing: SignalIntakeResultRow,
    fingerprint: str,
) -> SignalIntakeItemResult:
    if existing.command_fingerprint != fingerprint:
        raise SourceEventConflict()
    return SignalIntakeItemResult.model_validate(
        {
            "signal_event_id": existing.signal_event_id,
            "alert_id": existing.alert_id,
            "outcome": existing.outcome,
            "replayed": True,
        }
    )


def _counts(items: tuple[SignalIntakeItemResult, ...]) -> SignalIntakeCounts:
    values = {
        "opened": 0,
        "updated": 0,
        "resolved": 0,
        "reopened": 0,
        "stale": 0,
        "orphan_resolved": 0,
        "replayed": 0,
    }
    for item in items:
        key = "replayed" if item.replayed else item.outcome
        values[key] += 1
    return SignalIntakeCounts.model_validate(values)


def _audit_action(outcome: ProjectionOutcome) -> str:
    return {
        "opened": "alert.opened",
        "updated": "alert.updated",
        "resolved": "alert.resolved",
        "reopened": "alert.reopened",
        "stale": "alert.stale_signal_ignored",
        "orphan_resolved": "alert.orphan_resolved_ignored",
    }[outcome]
