from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError, OperationalError

from incident_intelligence.domain.alerts import project_alert
from incident_intelligence.domain.forbidden_identity import reject_forbidden_identity
from incident_intelligence.domain.models import SignalEvent
from incident_intelligence.domain.signal_intake import ProjectionOutcome, SignalCommand
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.alert_lifecycle_repository import AlertLifecycleRepository
from incident_intelligence.persistence.alert_source_repository import AlertSourceRepository
from incident_intelligence.persistence.incident_repository import (
    IncidentEvaluationJobRecord,
    IncidentEvaluationJobRepository,
)
from incident_intelligence.persistence.models import SignalIntakeResultRow
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
    ignored: int = 0


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
        input_count: int | None = None,
        ignored_count: int = 0,
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
                    input_count=input_count,
                    ignored_count=ignored_count,
                )
            except IntegrityError:
                if attempt == 2:
                    raise
            except OperationalError as error:
                if attempt == 2 or not _is_retryable_mysql_lock_error(error):
                    raise
        raise RuntimeError("signal_intake_retry_exhausted")

    def _apply_source_environments(
        self, commands: Sequence[SignalCommand]
    ) -> tuple[SignalCommand, ...]:
        with self._uow_factory() as uow:
            sources = _alert_sources(uow)
            result: list[SignalCommand] = []
            for command in commands:
                source = sources.find_source(command.alert_source_id)
                if source is None:
                    raise RuntimeError("signal_alert_source_not_found")
                if not source.environment_configured:
                    result.append(command)
                    continue
                reason_codes = command.normalization_reason_codes
                if command.environment not in {"unknown", source.environment}:
                    kept_codes = tuple(
                        code
                        for code in reason_codes
                        if code != "source_environment_overrode_payload"
                    )[:9]
                    reason_codes = (*kept_codes, "source_environment_overrode_payload")
                result.append(
                    command.model_copy(
                        update={
                            "environment": source.environment,
                            "normalization_reason_codes": reason_codes,
                        }
                    )
                )
            return tuple(result)

    def _submit_once(
        self,
        commands: Sequence[SignalCommand],
        fingerprints: tuple[str, ...],
        actor: str,
        request_id: str,
        receipt_context: ReceiptContext | None,
        *,
        input_count: int | None,
        ignored_count: int,
    ) -> SignalIntakeBatchResult:
        with self._uow_factory() as uow:
            records = _records(uow)
            alert_lifecycles = _alert_lifecycles(uow)
            incident_evaluation_jobs = _incident_evaluation_jobs(uow)
            items = tuple(
                self._submit_command(
                    records,
                    alert_lifecycles,
                    incident_evaluation_jobs,
                    command,
                    fingerprint,
                    actor,
                    request_id,
                )
                for command, fingerprint in zip(commands, fingerprints, strict=True)
            )
            counts = _counts(items, ignored_count=ignored_count)
            if receipt_context is not None:
                _validate_receipt_context(commands, receipt_context)
                replayed_batch = bool(items) and all(item.replayed for item in items)
                record_receipt(
                    _alert_sources(uow),
                    id_factory=self._id_factory,
                    context=receipt_context,
                    outcome="REPLAYED" if replayed_batch else "ACCEPTED",
                    reason_code=(
                        "exact_batch_replay"
                        if replayed_batch
                        else "watchdog_ignored"
                        if not items and ignored_count
                        else "signal_batch_accepted"
                    ),
                    input_count=input_count if input_count is not None else len(commands),
                    counts=ReceiptCounts(
                        opened=counts.opened,
                        updated=counts.updated,
                        resolved=counts.resolved,
                        replayed=counts.replayed,
                        ignored=counts.ignored,
                    ),
                )
            uow.commit()
        return SignalIntakeBatchResult(items=items, counts=counts)

    def _submit_command(
        self,
        records: RecordRepositories,
        alert_lifecycles: AlertLifecycleRepository,
        incident_evaluation_jobs: IncidentEvaluationJobRepository,
        command: SignalCommand,
        fingerprint: str,
        actor: str,
        request_id: str,
    ) -> SignalIntakeItemResult:
        existing = records.find_signal_result(
            command.alert_source_id, command.source, command.source_event_id
        )
        if existing is not None:
            return _replay_result(existing, fingerprint)

        now = self._clock().astimezone(UTC)
        signal = SignalEvent(
            id=self._id_factory("sig"),
            alert_source_id=command.alert_source_id,
            source=command.source,
            source_event_id=command.source_event_id,
            source_alert_key=command.source_alert_key,
            episode_started_at=command.episode_started_at,
            event_type=command.event_type,
            alert_name=command.alert_name,
            title=command.title,
            summary=command.summary,
            description=command.description,
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
        existing_alert = alert_lifecycles.get_by_identity(
            alert_source_id=command.alert_source_id,
            source_alert_key=command.source_alert_key,
            episode_started_at=command.episode_started_at,
            for_update=True,
        )
        projection = project_alert(
            existing_alert,
            command,
            received_at=now,
            alert_id=self._id_factory("alt") if existing_alert is None else None,
        )
        if projection.created:
            alert_lifecycles.insert(projection.alert)
        elif not alert_lifecycles.update(
            projection.alert,
            expected_version=projection.alert.version - 1,
        ):
            raise RuntimeError("alert_lifecycle_concurrent_update")
        incident_evaluation_jobs.enqueue(
            IncidentEvaluationJobRecord(
                id=self._id_factory("iej"),
                alert_id=projection.alert.id,
                alert_version=projection.alert.version,
                state="PENDING",
                attempt_count=0,
                available_at=now,
                lease_owner=None,
                lease_expires_at=None,
                last_error_code=None,
                outcome=None,
                reason_codes=(),
                incident_ids=(),
                created_at=now,
                updated_at=now,
            )
        )
        outcome: ProjectionOutcome = projection.outcome
        records.add_signal_result(
            SignalIntakeResultRow(
                alert_source_id=command.alert_source_id,
                source=command.source,
                source_event_id=command.source_event_id,
                command_fingerprint=fingerprint,
                signal_event_id=signal.id,
                alert_id=None,
                alert_lifecycle_id=projection.alert.id,
                outcome=outcome,
                created_at=now,
            )
        )
        audit_details = {
            "reason_code": "external_signal_received",
            "adapter": command.source,
        }
        if command.normalization_reason_codes:
            audit_details["normalization_reason_codes"] = ",".join(
                command.normalization_reason_codes
            )
        records.add_audit(
            audit_id=self._id_factory("aud"),
            actor=actor,
            action="signal.received",
            resource_type="signal_event",
            resource_id=signal.id,
            request_id=request_id,
            details=audit_details,
            created_at=now,
        )
        records.add_audit(
            audit_id=self._id_factory("aud"),
            actor=actor,
            action=f"alert.{outcome}",
            resource_type="alert",
            resource_id=projection.alert.id,
            request_id=request_id,
            details={
                "reason_code": f"alert_lifecycle_{outcome}",
                "signal_event_id": signal.id,
            },
            created_at=now,
        )
        return SignalIntakeItemResult(
            signal_event_id=signal.id,
            alert_id=projection.alert.id,
            outcome=outcome,
            replayed=False,
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


def _alert_sources(uow: SqlAlchemyUnitOfWork) -> AlertSourceRepository:
    if uow.alert_sources is None:
        raise RuntimeError("工作单元没有可用告警源仓储")
    return uow.alert_sources


def _alert_lifecycles(uow: SqlAlchemyUnitOfWork) -> AlertLifecycleRepository:
    if uow.alert_lifecycles is None:
        raise RuntimeError("工作单元没有可用告警生命周期仓储")
    return uow.alert_lifecycles


def _incident_evaluation_jobs(
    uow: SqlAlchemyUnitOfWork,
) -> IncidentEvaluationJobRepository:
    if uow.incident_evaluation_jobs is None:
        raise RuntimeError("工作单元没有可用 Incident 评估任务仓储")
    return uow.incident_evaluation_jobs


def _validate_receipt_context(commands: Sequence[SignalCommand], context: ReceiptContext) -> None:
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


def _replay_result(existing: SignalIntakeResultRow, fingerprint: str) -> SignalIntakeItemResult:
    if existing.command_fingerprint != fingerprint:
        raise SourceEventConflict()
    return SignalIntakeItemResult(
        signal_event_id=existing.signal_event_id,
        alert_id=existing.alert_lifecycle_id,
        outcome=cast(ProjectionOutcome, existing.outcome),
        replayed=True,
    )


def _counts(
    items: tuple[SignalIntakeItemResult, ...], *, ignored_count: int = 0
) -> SignalIntakeCounts:
    opened = sum(not item.replayed and item.outcome == "opened" for item in items)
    updated = sum(not item.replayed and item.outcome == "updated" for item in items)
    resolved = sum(
        not item.replayed and item.outcome in {"resolved", "orphan_resolved"} for item in items
    )
    replayed = sum(item.replayed for item in items)
    return SignalIntakeCounts(
        opened=opened,
        updated=updated,
        resolved=resolved,
        replayed=replayed,
        ignored=ignored_count,
    )


def _is_retryable_mysql_lock_error(error: OperationalError) -> bool:
    arguments = cast(tuple[object, ...], getattr(error.orig, "args", ()))
    return bool(arguments) and arguments[0] in {1205, 1213}
