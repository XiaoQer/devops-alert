from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from pydantic import BaseModel, ConfigDict

from incident_intelligence.domain.diagnosis import (
    DiagnosisAlertFact,
    DiagnosisReference,
    DiagnosisSnapshot,
)
from incident_intelligence.domain.evidence import EvidenceItem
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.diagnosis_repository import (
    DiagnosisRunRepository,
    DiagnosisToolReceiptRecord,
    DiagnosisToolReceiptRepository,
)
from incident_intelligence.persistence.evidence_repository import EvidenceRunRepository
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.diagnosis_capabilities import DiagnosisCapabilityIssuer

MAX_SNAPSHOT_BYTES = 40 * 1024
MAX_EVIDENCE_DETAIL_BYTES = 8 * 1024


class DiagnosisSnapshotToolResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    diagnosis_run_id: str
    incident_id: str
    environment: str
    service_name: str | None
    alert_names: tuple[str, ...]
    alert_facts: tuple[DiagnosisAlertFact, ...]
    evidence_references: tuple[DiagnosisReference, ...]
    knowledge_references: tuple[DiagnosisReference, ...]
    truncated: bool


class DiagnosisEvidenceDetailToolResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    display_name: str
    source_type: str
    state: str
    evidence_type: str
    baseline_summary: dict[str, str | int | float | bool | None]
    fault_summary: dict[str, str | int | float | bool | None]
    interpretation: str | None
    truncated: bool


@dataclass(frozen=True, slots=True)
class DiagnosisToolRunNotFound(Exception):
    reason_code: str = "diagnosis_run_not_found"

    def __str__(self) -> str:
        return self.reason_code


@dataclass(frozen=True, slots=True)
class DiagnosisEvidenceScopeDenied(Exception):
    reason_code: str = "diagnosis_evidence_scope_denied"

    def __str__(self) -> str:
        return self.reason_code


class DiagnosisToolService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        capability_issuer: DiagnosisCapabilityIssuer,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[IdPrefix], str] = new_id,
    ) -> None:
        self._uow_factory = uow_factory
        self._capability_issuer = capability_issuer
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory

    def get_diagnosis_snapshot(
        self,
        diagnosis_run_id: str,
        *,
        capability_token: str,
    ) -> DiagnosisSnapshotToolResult:
        self._capability_issuer.verify(capability_token, expected_run_id=diagnosis_run_id)
        with self._uow_factory() as uow:
            snapshot = _runs(uow).get_snapshot(diagnosis_run_id)
            if snapshot is None:
                raise DiagnosisToolRunNotFound()
            result = _bounded_snapshot_projection(snapshot)
            self._audit(
                uow,
                diagnosis_run_id=diagnosis_run_id,
                tool_key="get_diagnosis_snapshot",
                request_hash=_request_hash(
                    diagnosis_run_id,
                    tool_key="get_diagnosis_snapshot",
                    target_id="snapshot",
                ),
                result_hash=_content_hash(result.model_dump(mode="json")),
                truncated=result.truncated,
                error_code=None,
            )
            uow.commit()
            return result

    def get_evidence_detail(
        self,
        diagnosis_run_id: str,
        *,
        capability_token: str,
        evidence_item_id: str,
    ) -> DiagnosisEvidenceDetailToolResult:
        self._capability_issuer.verify(capability_token, expected_run_id=diagnosis_run_id)
        request_hash = _request_hash(
            diagnosis_run_id,
            tool_key="get_evidence_detail",
            target_id=evidence_item_id,
        )
        with self._uow_factory() as uow:
            snapshot = _runs(uow).get_snapshot(diagnosis_run_id)
            if snapshot is None:
                raise DiagnosisToolRunNotFound()
            reference = snapshot.allowed_references().get(evidence_item_id)
            if reference is None or reference.kind != "EVIDENCE":
                self._audit(
                    uow,
                    diagnosis_run_id=diagnosis_run_id,
                    tool_key="get_evidence_detail",
                    request_hash=request_hash,
                    result_hash=None,
                    truncated=False,
                    error_code="diagnosis_evidence_scope_denied",
                )
                uow.commit()
                raise DiagnosisEvidenceScopeDenied()
            item = _evidence_runs(uow).get_item(evidence_item_id)
            if item is None or item.evidence_run_id != snapshot.evidence_run_id:
                self._audit(
                    uow,
                    diagnosis_run_id=diagnosis_run_id,
                    tool_key="get_evidence_detail",
                    request_hash=request_hash,
                    result_hash=None,
                    truncated=False,
                    error_code="diagnosis_evidence_scope_denied",
                )
                uow.commit()
                raise DiagnosisEvidenceScopeDenied()
            result = _bounded_evidence_projection(item)
            self._audit(
                uow,
                diagnosis_run_id=diagnosis_run_id,
                tool_key="get_evidence_detail",
                request_hash=request_hash,
                result_hash=_content_hash(result.model_dump(mode="json")),
                truncated=result.truncated,
                error_code=None,
            )
            uow.commit()
            return result

    def _audit(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        diagnosis_run_id: str,
        tool_key: str,
        request_hash: str,
        result_hash: str | None,
        truncated: bool,
        error_code: str | None,
    ) -> None:
        receipts = _tool_receipts(uow)
        call_index = 1 + sum(
            receipt.tool_key == tool_key for receipt in receipts.list_for_run(diagnosis_run_id)
        )
        receipts.insert(
            DiagnosisToolReceiptRecord(
                id=self._id_factory("dtool"),
                diagnosis_run_id=diagnosis_run_id,
                tool_key=tool_key,
                call_index=call_index,
                request_hash=request_hash,
                result_hash=result_hash,
                truncated=truncated,
                error_code=error_code,
                created_at=self._clock().astimezone(UTC),
            )
        )


def _request_hash(diagnosis_run_id: str, *, tool_key: str, target_id: str) -> str:
    return _content_hash(
        {"diagnosis_run_id": diagnosis_run_id, "target_id": target_id, "tool_key": tool_key}
    )


def _content_hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _payload_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode())


def _bounded_snapshot_projection(snapshot: DiagnosisSnapshot) -> DiagnosisSnapshotToolResult:
    alert_names = list(snapshot.alert_names[:100])
    alert_facts = list(snapshot.alert_facts[:100])
    evidence_references = list(snapshot.evidence_references[:50])
    knowledge_references = list(snapshot.knowledge_references[:50])
    truncated = (
        len(alert_names) < len(snapshot.alert_names)
        or len(alert_facts) < len(snapshot.alert_facts)
        or len(evidence_references) < len(snapshot.evidence_references)
        or len(knowledge_references) < len(snapshot.knowledge_references)
    )
    while True:
        result = DiagnosisSnapshotToolResult(
            diagnosis_run_id=snapshot.diagnosis_run_id,
            incident_id=snapshot.incident_id,
            environment=snapshot.environment,
            service_name=snapshot.service_name,
            alert_names=tuple(alert_names),
            alert_facts=tuple(alert_facts),
            evidence_references=tuple(evidence_references),
            knowledge_references=tuple(knowledge_references),
            truncated=truncated,
        )
        if _payload_size(result.model_dump(mode="json")) <= MAX_SNAPSHOT_BYTES:
            return result
        truncated = True
        if alert_facts:
            alert_facts.pop()
        elif evidence_references:
            evidence_references.pop()
        elif knowledge_references:
            knowledge_references.pop()
        elif alert_names:
            alert_names.pop()
        else:
            return result


def _bounded_evidence_projection(item: EvidenceItem) -> DiagnosisEvidenceDetailToolResult:
    baseline_summary = dict(item.baseline_summary)
    fault_summary = dict(item.fault_summary)
    interpretation = item.interpretation
    truncated = False
    while True:
        result = DiagnosisEvidenceDetailToolResult(
            id=item.id,
            display_name=item.display_name,
            source_type=item.source_type,
            state=item.state,
            evidence_type=item.evidence_type,
            baseline_summary=baseline_summary,
            fault_summary=fault_summary,
            interpretation=interpretation,
            truncated=truncated,
        )
        if _payload_size(result.model_dump(mode="json")) <= MAX_EVIDENCE_DETAIL_BYTES:
            return result
        truncated = True
        if fault_summary:
            fault_summary.pop(next(reversed(fault_summary)))
        elif baseline_summary:
            baseline_summary.pop(next(reversed(baseline_summary)))
        elif interpretation:
            interpretation = interpretation[: len(interpretation) // 2]
        else:
            return result


def _runs(uow: SqlAlchemyUnitOfWork) -> DiagnosisRunRepository:
    if uow.diagnosis_runs is None:
        raise RuntimeError("诊断运行仓储尚未初始化")
    return uow.diagnosis_runs


def _tool_receipts(uow: SqlAlchemyUnitOfWork) -> DiagnosisToolReceiptRepository:
    receipts = uow.diagnosis_tool_receipts
    if receipts is None:
        raise RuntimeError("诊断工具审计仓储尚未初始化")
    return receipts


def _evidence_runs(uow: SqlAlchemyUnitOfWork) -> EvidenceRunRepository:
    if uow.evidence_runs is None:
        raise RuntimeError("取证运行仓储尚未初始化")
    return uow.evidence_runs
