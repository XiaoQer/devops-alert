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
from incident_intelligence.persistence.alert_source_repository import AlertSourceRepository
from incident_intelligence.persistence.correlation_repository import CorrelationRepository
from incident_intelligence.persistence.models import AlertRow, SignalIntakeResultRow
from incident_intelligence.persistence.repositories import RecordRepositories
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.source_receipts import (
    ReceiptContext,
    ReceiptCounts,
    record_receipt,
)


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
        receipt_context: ReceiptContext | None = None,
    ) -> SignalIntakeBatchResult:
        for command in commands:
            reject_forbidden_identity(command.model_dump(mode="json"))
        fingerprints = tuple(_fingerprint(command) for command in commands)
        try:
            return self._submit_once(
                commands,
                fingerprints,
                actor,
                request_id,
                receipt_context,
            )
        except IntegrityError:
            return self._submit_once(
                commands,
                fingerprints,
                actor,
                request_id,
                receipt_context,
            )

    def _submit_once(
        self,
        commands: Sequence[SignalCommand],
        fingerprints: tuple[str, ...],
        actor: str,
        request_id: str,
        receipt_context: ReceiptContext | None,
    ) -> SignalIntakeBatchResult:
        with self._uow_factory() as uow:
            records = _records(uow)
            correlation = _correlation(uow)
            items = tuple(
                self._submit_command(
                    records,
                    correlation,
                    command,
                    fingerprint,
                    actor,
                    request_id,
                )
                for command, fingerprint in zip(commands, fingerprints, strict=True)
            )
            counts = _counts(items)
            if receipt_context is not None:
                _validate_receipt_context(commands, receipt_context)
                record_receipt(
                    _alert_sources(uow),
                    id_factory=self._id_factory,
                    context=receipt_context,
                    outcome="REPLAYED" if all(item.replayed for item in items) else "ACCEPTED",
                    reason_code=(
                        "exact_batch_replay"
                        if all(item.replayed for item in items)
                        else "signal_batch_accepted"
                    ),
                    input_count=len(commands),
                    counts=ReceiptCounts(
                        opened=counts.opened + counts.reopened,
                        updated=counts.updated,
                        resolved=counts.resolved,
                        replayed=counts.replayed,
                        ignored=counts.stale + counts.orphan_resolved,
                    ),
                )
            uow.commit()
        return SignalIntakeBatchResult(
            items=items,
            counts=counts,
        )

    def _submit_command(
        self,
        records: RecordRepositories,
        correlation: CorrelationRepository,
        command: SignalCommand,
        fingerprint: str,
        actor: str,
        request_id: str,
    ) -> SignalIntakeItemResult:
        existing = records.find_signal_result(
            command.alert_source_id,
            command.source,
            command.source_event_id,
        )
        if existing is not None:
            return _replay_result(existing, fingerprint)

        now = self._clock().astimezone(UTC)
        signal = SignalEvent(
            id=self._id_factory("sig"),
            alert_source_id=command.alert_source_id,
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
            command.alert_source_id,
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
        if decision.changes_projection and decision.alert is not None:
            correlation.enqueue(
                alert_source_id=decision.alert.alert_source_id,
                alert_id=decision.alert.id,
                alert_version=decision.alert.version,
                now=now,
            )
        records.add_signal_result(
            SignalIntakeResultRow(
                alert_source_id=command.alert_source_id,
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


def _correlation(uow: SqlAlchemyUnitOfWork) -> CorrelationRepository:
    if uow.correlation is None:
        raise RuntimeError("工作单元没有可用关联仓储")
    return uow.correlation


def _alert_sources(uow: SqlAlchemyUnitOfWork) -> AlertSourceRepository:
    if uow.alert_sources is None:
        raise RuntimeError("工作单元没有可用告警源仓储")
    return uow.alert_sources


def _validate_receipt_context(
    commands: Sequence[SignalCommand],
    context: ReceiptContext,
) -> None:
    expected_source = {
        "ALERTMANAGER": "alertmanager",
        "CLOUDEVENTS": "cloudevents",
        "MANUAL": "manual",
    }[context.adapter_type]
    if any(
        command.alert_source_id != context.alert_source_id or command.source != expected_source
        for command in commands
    ):
        raise ValueError("接收上下文与信号命令来源不一致")


def _alert_from_row(row: AlertRow) -> Alert:
    return Alert.model_validate(
        {
            "id": row.id,
            "signal_event_id": row.signal_event_id,
            "alert_source_id": row.alert_source_id,
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
