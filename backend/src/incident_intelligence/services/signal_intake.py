from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError, OperationalError

from incident_intelligence.domain.forbidden_identity import reject_forbidden_identity
from incident_intelligence.domain.models import Alert, SignalEvent
from incident_intelligence.domain.signal_intake import (
    ProjectionOutcome,
    SignalCommand,
    decide_alert_projection,
)
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.alert_group_repository import AlertGroupRepository
from incident_intelligence.persistence.alert_source_repository import AlertSourceRepository
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
        effective_commands = self._apply_source_environments(commands)
        fingerprints = tuple(_fingerprint(command) for command in effective_commands)
        for attempt in range(3):
            try:
                return self._submit_once(
                    effective_commands,
                    fingerprints,
                    actor,
                    request_id,
                    receipt_context,
                )
            except IntegrityError:
                if attempt == 2:
                    raise
            except OperationalError as error:
                if attempt == 2 or not _is_retryable_mysql_lock_error(error):
                    raise
        raise RuntimeError("signal_intake_retry_exhausted")

    def _apply_source_environments(
        self,
        commands: Sequence[SignalCommand],
    ) -> tuple[SignalCommand, ...]:
        with self._uow_factory() as uow:
            sources = _alert_sources(uow)
            result: list[SignalCommand] = []
            for command in commands:
                source = sources.find_source(command.alert_source_id)
                if source is None:
                    raise RuntimeError("signal_alert_source_not_found")
                if source.environment_configured:
                    reason_codes = command.normalization_reason_codes
                    if command.environment not in {"unknown", source.environment}:
                        filtered_reason_codes = tuple(
                            code
                            for code in reason_codes
                            if code != "source_environment_overrode_payload"
                        )
                        reason_codes = (
                            *filtered_reason_codes[:9],
                            "source_environment_overrode_payload",
                        )
                    result.append(
                        command.model_copy(
                            update={
                                "environment": source.environment,
                                "normalization_reason_codes": reason_codes,
                            }
                        )
                    )
                else:
                    result.append(command)
            return tuple(result)

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
            alert_groups = _alert_groups(uow)
            items = tuple(
                self._submit_command(
                    records,
                    alert_groups,
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
        alert_groups: AlertGroupRepository,
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
            entity_type=command.entity_type,
            entity_key=command.entity_key,
            entity_display_name=command.entity_display_name,
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
            alert_groups.enqueue_grouping(
                alert_id=decision.alert.id,
                alert_cycle=decision.alert.cycle,
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
            normalization_reason_codes=command.normalization_reason_codes,
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
        normalization_reason_codes: tuple[str, ...] = (),
    ) -> None:
        details = {"reason_code": reason_code, "adapter": adapter}
        if parent_id is not None:
            details["parent_id"] = parent_id
        if normalization_reason_codes:
            details["normalization_reason_codes"] = ",".join(normalization_reason_codes)
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


def _alert_groups(uow: SqlAlchemyUnitOfWork) -> AlertGroupRepository:
    if uow.alert_groups is None:
        raise RuntimeError("工作单元没有可用告警组仓储")
    return uow.alert_groups


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
            "cycle": row.cycle,
            "title": row.title,
            "severity": row.severity,
            "service": row.service,
            "entity_type": row.entity_type,
            "entity_key": row.entity_key,
            "entity_display_name": row.entity_display_name,
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


def _is_retryable_mysql_lock_error(error: OperationalError) -> bool:
    arguments = cast(tuple[object, ...], getattr(error.orig, "args", ()))
    return bool(arguments) and arguments[0] in {1205, 1213}
