from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError

from incident_intelligence.domain.evidence import (
    EvidenceContext,
    EvidenceItem,
    EvidenceRun,
    build_evidence_window,
)
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.alert_center_repository import AlertRepository
from incident_intelligence.persistence.evidence_repository import (
    EvidenceOperationRecord,
    EvidenceOperationRepository,
    EvidenceRunRepository,
    EvidenceTaskRecord,
)
from incident_intelligence.persistence.incident_repository import IncidentRepository
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork


class EvidenceRunPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[EvidenceRun, ...]
    total: int
    limit: int
    offset: int


class EvidenceRunDetail(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run: EvidenceRun
    items: tuple[EvidenceItem, ...]
    items_truncated: bool


class EvidenceRunMutationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run: EvidenceRun
    replayed: bool


@dataclass(frozen=True, slots=True)
class EvidenceRunNotFound(Exception):
    reason_code: str = "evidence_run_not_found"


@dataclass(frozen=True, slots=True)
class EvidenceRunAlreadyActive(Exception):
    active_run_id: str
    reason_code: str = "evidence_run_already_active"


@dataclass(frozen=True, slots=True)
class EvidenceIdempotencyConflict(Exception):
    reason_code: str = "idempotency_conflict"


class IncidentEvidenceService:
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

    def list_runs(
        self,
        incident_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> EvidenceRunPage:
        if not 1 <= limit <= 50 or not 0 <= offset <= 10_000:
            raise ValueError("invalid_evidence_run_page")
        with self._uow_factory() as uow:
            _require_incident(uow, incident_id)
            items, total = _runs(uow).list_for_incident(
                incident_id,
                limit=limit,
                offset=offset,
            )
            return EvidenceRunPage(items=items, total=total, limit=limit, offset=offset)

    def get_run(self, incident_id: str, run_id: str) -> EvidenceRunDetail:
        with self._uow_factory() as uow:
            _require_incident(uow, incident_id)
            run = _runs(uow).get(run_id)
            if run is None or run.incident_id != incident_id:
                raise EvidenceRunNotFound()
            items = _runs(uow).list_items(run_id, limit=101)
            return EvidenceRunDetail(
                run=run,
                items=items[:100],
                items_truncated=len(items) > 100,
            )

    def request_manual(
        self,
        incident_id: str,
        *,
        idempotency_key: str,
        actor: str,
        request_id: str,
    ) -> EvidenceRunMutationResult:
        scope = f"incident:{incident_id}:evidence"
        key_hash = sha256(idempotency_key.encode()).hexdigest()
        fingerprint = sha256(b"request-manual-evidence:v1").hexdigest()
        try:
            with self._uow_factory() as uow:
                prior = _operations(uow).find(
                    scope=scope,
                    idempotency_key_hash=key_hash,
                )
                if prior is not None:
                    return self._replay(uow, prior, fingerprint)
                incident = _incidents(uow).get(incident_id, for_update=True)
                if incident is None:
                    raise EvidenceRunNotFound()
                active = _runs(uow).find_active(incident_id, for_update=True)
                if active is not None:
                    raise EvidenceRunAlreadyActive(active.id)
                alert_ids = _incidents(uow).list_alert_ids(incident_id)
                facts = _alerts(uow).list_evaluation_facts_by_ids(alert_ids)
                now = self._clock().astimezone(UTC)
                prior_run = _runs(uow).latest_for_incident(incident_id)
                if facts:
                    anchor_at = min(fact.first_received_at for fact in facts)
                    services = {fact.service for fact in facts if fact.service is not None}
                    service_name = next(iter(services)) if len(services) == 1 else None
                    alert_names = tuple(sorted({fact.alert_name for fact in facts}))
                elif prior_run is not None:
                    anchor_at = prior_run.anchor_at
                    service_name = prior_run.context.service_name
                    alert_names = prior_run.context.alert_names
                else:
                    raise EvidenceRunNotFound("incident_alerts_not_found")
                run = EvidenceRun(
                    id=self._id_factory("evr"),
                    incident_id=incident.id,
                    trigger="MANUAL",
                    state="QUEUED",
                    anchor_at=anchor_at,
                    window=build_evidence_window(anchor_at, now),
                    context=EvidenceContext(
                        environment=incident.environment,
                        service_name=service_name,
                        alert_names=alert_names,
                    ),
                    requested_by=actor,
                    created_at=now,
                )
                task = EvidenceTaskRecord(
                    id=self._id_factory("evtask"),
                    evidence_run_id=run.id,
                    state="PENDING",
                    attempt_count=0,
                    next_attempt_at=now,
                    lease_owner=None,
                    lease_until=None,
                    last_error_code=None,
                    completed_at=None,
                    created_at=now,
                    updated_at=now,
                )
                _runs(uow).insert_run_with_task(run, task)
                _operations(uow).insert(
                    EvidenceOperationRecord(
                        id=self._id_factory("evop"),
                        scope=scope,
                        idempotency_key_hash=key_hash,
                        command_fingerprint=fingerprint,
                        evidence_run_id=run.id,
                        actor=actor,
                        request_id=request_id,
                        completed_at=now,
                    )
                )
                uow.commit()
                return EvidenceRunMutationResult(run=run, replayed=False)
        except IntegrityError:
            with self._uow_factory() as uow:
                prior = _operations(uow).find(
                    scope=scope,
                    idempotency_key_hash=key_hash,
                )
                if prior is not None:
                    return self._replay(uow, prior, fingerprint)
            raise

    def _replay(
        self,
        uow: SqlAlchemyUnitOfWork,
        operation: EvidenceOperationRecord,
        fingerprint: str,
    ) -> EvidenceRunMutationResult:
        if operation.command_fingerprint != fingerprint:
            raise EvidenceIdempotencyConflict()
        run = _runs(uow).get(operation.evidence_run_id)
        if run is None:
            raise EvidenceRunNotFound()
        return EvidenceRunMutationResult(run=run, replayed=True)


def _require_incident(uow: SqlAlchemyUnitOfWork, incident_id: str) -> None:
    if _incidents(uow).get(incident_id) is None:
        raise EvidenceRunNotFound("incident_not_found")


def _incidents(uow: SqlAlchemyUnitOfWork) -> IncidentRepository:
    if uow.incidents is None:
        raise RuntimeError("Incident 仓储尚未初始化")
    return uow.incidents


def _alerts(uow: SqlAlchemyUnitOfWork) -> AlertRepository:
    if uow.alerts is None:
        raise RuntimeError("告警仓储尚未初始化")
    return uow.alerts


def _runs(uow: SqlAlchemyUnitOfWork) -> EvidenceRunRepository:
    if uow.evidence_runs is None:
        raise RuntimeError("取证运行仓储尚未初始化")
    return uow.evidence_runs


def _operations(uow: SqlAlchemyUnitOfWork) -> EvidenceOperationRepository:
    if uow.evidence_operations is None:
        raise RuntimeError("取证操作仓储尚未初始化")
    return uow.evidence_operations
