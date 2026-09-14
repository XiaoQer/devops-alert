from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256

from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError

from incident_intelligence.domain.diagnosis import (
    DiagnosisAlertFact,
    DiagnosisReference,
    DiagnosisRun,
    DiagnosisSnapshot,
)
from incident_intelligence.domain.evidence import EvidenceItem
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.alert_center_repository import AlertRepository
from incident_intelligence.persistence.diagnosis_repository import (
    DiagnosisOperationRecord,
    DiagnosisOperationRepository,
    DiagnosisRunRepository,
    DiagnosisTaskRecord,
)
from incident_intelligence.persistence.evidence_repository import EvidenceRunRepository
from incident_intelligence.persistence.incident_repository import IncidentRepository
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork


class DiagnosisRunPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[DiagnosisRun, ...]
    total: int
    limit: int
    offset: int


class DiagnosisRunDetail(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run: DiagnosisRun
    snapshot: DiagnosisSnapshot


class DiagnosisRunMutationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run: DiagnosisRun
    replayed: bool


class DiagnosisRunNotFound(ValueError):
    def __init__(self, reason_code: str = "diagnosis_run_not_found") -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class DiagnosisEvidenceRunInvalid(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class DiagnosisRunAlreadyActive(ValueError):
    def __init__(self, active_run_id: str) -> None:
        super().__init__("diagnosis_run_already_active")
        self.active_run_id = active_run_id
        self.reason_code = "diagnosis_run_already_active"


class IncidentDiagnosisService:
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

    def request_manual(
        self,
        incident_id: str,
        *,
        evidence_run_id: str,
        actor: str,
        idempotency_key: str,
        request_id: str,
    ) -> DiagnosisRunMutationResult:
        scope = f"incident:{incident_id}:diagnosis"
        key_hash = sha256(idempotency_key.encode()).hexdigest()
        try:
            with self._uow_factory() as uow:
                prior = _operations(uow).find(
                    scope=scope,
                    idempotency_key_hash=key_hash,
                )
                if prior is not None:
                    return self._replay(uow, prior.diagnosis_run_id)
                incident = _incidents(uow).get(incident_id, for_update=True)
                if incident is None:
                    raise DiagnosisRunNotFound("incident_not_found")
                active = _runs(uow).find_active(incident_id, for_update=True)
                if active is not None:
                    raise DiagnosisRunAlreadyActive(active.id)
                evidence_run = _evidence_runs(uow).get(evidence_run_id)
                if evidence_run is None or evidence_run.incident_id != incident_id:
                    raise DiagnosisEvidenceRunInvalid("diagnosis_evidence_run_not_found")
                if evidence_run.state not in {"SUCCEEDED", "PARTIAL"}:
                    raise DiagnosisEvidenceRunInvalid("diagnosis_evidence_run_not_ready")
                alert_ids = _incidents(uow).list_alert_ids(incident_id)
                if len(alert_ids) > 500:
                    raise DiagnosisEvidenceRunInvalid("diagnosis_alerts_exceed_limit")
                alert_facts = _alerts(uow).list_evaluation_facts_by_ids(alert_ids)
                if any(fact.environment != incident.environment for fact in alert_facts):
                    raise DiagnosisEvidenceRunInvalid("diagnosis_alert_scope_invalid")

                now = self._clock().astimezone(UTC)
                run = DiagnosisRun(
                    id=self._id_factory("drun"),
                    incident_id=incident_id,
                    evidence_run_id=evidence_run_id,
                    state="QUEUED",
                    requested_by=actor,
                    created_at=now,
                )
                snapshot = DiagnosisSnapshot(
                    diagnosis_run_id=run.id,
                    incident_id=incident_id,
                    evidence_run_id=evidence_run_id,
                    environment=incident.environment,
                    service_name=evidence_run.context.service_name,
                    alert_names=evidence_run.context.alert_names,
                    alert_facts=tuple(
                        DiagnosisAlertFact(
                            id=fact.id,
                            alert_name=fact.alert_name,
                            state=fact.state,
                            severity=fact.severity,
                            first_received_at=fact.first_received_at,
                        )
                        for fact in alert_facts
                    ),
                    evidence_references=_evidence_references(
                        _evidence_runs(uow).list_items(evidence_run_id, limit=101)
                    ),
                    created_at=now,
                )
                task = DiagnosisTaskRecord(
                    id=self._id_factory("dtask"),
                    diagnosis_run_id=run.id,
                    state="PENDING",
                    attempt_count=0,
                    next_attempt_at=now,
                    lease_owner=None,
                    lease_until=None,
                    last_error_code=None,
                    created_at=now,
                    updated_at=now,
                )
                _runs(uow).insert_run_with_snapshot_and_task(run, snapshot, task)
                _operations(uow).insert(
                    DiagnosisOperationRecord(
                        id=self._id_factory("dtool"),
                        scope=scope,
                        idempotency_key_hash=key_hash,
                        diagnosis_run_id=run.id,
                        created_at=now,
                    )
                )
                uow.commit()
                return DiagnosisRunMutationResult(run=run, replayed=False)
        except IntegrityError:
            with self._uow_factory() as uow:
                prior = _operations(uow).find(
                    scope=scope,
                    idempotency_key_hash=key_hash,
                )
                if prior is not None:
                    return self._replay(uow, prior.diagnosis_run_id)
            raise

    def list_runs(
        self,
        incident_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> DiagnosisRunPage:
        if not 1 <= limit <= 50 or not 0 <= offset <= 10_000:
            raise ValueError("invalid_diagnosis_run_page")
        with self._uow_factory() as uow:
            if _incidents(uow).get(incident_id) is None:
                raise DiagnosisRunNotFound("incident_not_found")
            items, total = _runs(uow).list_for_incident(incident_id, limit=limit, offset=offset)
            return DiagnosisRunPage(items=items, total=total, limit=limit, offset=offset)

    def get_run(self, incident_id: str, run_id: str) -> DiagnosisRunDetail:
        with self._uow_factory() as uow:
            if _incidents(uow).get(incident_id) is None:
                raise DiagnosisRunNotFound("incident_not_found")
            run = _runs(uow).get(run_id)
            snapshot = _runs(uow).get_snapshot(run_id)
            if run is None or snapshot is None or run.incident_id != incident_id:
                raise DiagnosisRunNotFound()
            return DiagnosisRunDetail(run=run, snapshot=snapshot)

    def _replay(
        self,
        uow: SqlAlchemyUnitOfWork,
        diagnosis_run_id: str,
    ) -> DiagnosisRunMutationResult:
        run = _runs(uow).get(diagnosis_run_id)
        if run is None:
            raise DiagnosisRunNotFound()
        return DiagnosisRunMutationResult(run=run, replayed=True)


def _evidence_references(items: tuple[EvidenceItem, ...]) -> tuple[DiagnosisReference, ...]:
    references: list[DiagnosisReference] = []
    for item in items[:100]:
        payload = item.model_dump(mode="json")
        references.append(
            DiagnosisReference(
                kind="EVIDENCE",
                target_id=item.id,
                content_hash=sha256(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode()
                ).hexdigest(),
            )
        )
    return tuple(references)


def _incidents(uow: SqlAlchemyUnitOfWork) -> IncidentRepository:
    if uow.incidents is None:
        raise RuntimeError("Incident 仓储尚未初始化")
    return uow.incidents


def _evidence_runs(uow: SqlAlchemyUnitOfWork) -> EvidenceRunRepository:
    if uow.evidence_runs is None:
        raise RuntimeError("取证运行仓储尚未初始化")
    return uow.evidence_runs


def _alerts(uow: SqlAlchemyUnitOfWork) -> AlertRepository:
    if uow.alerts is None:
        raise RuntimeError("告警仓储尚未初始化")
    return uow.alerts


def _runs(uow: SqlAlchemyUnitOfWork) -> DiagnosisRunRepository:
    if uow.diagnosis_runs is None:
        raise RuntimeError("诊断运行仓储尚未初始化")
    return uow.diagnosis_runs


def _operations(uow: SqlAlchemyUnitOfWork) -> DiagnosisOperationRepository:
    if uow.diagnosis_operations is None:
        raise RuntimeError("诊断操作仓储尚未初始化")
    return uow.diagnosis_operations
