from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from incident_intelligence.domain.diagnosis import DiagnosisReference, DiagnosisSnapshot
from incident_intelligence.domain.evidence import EvidenceItem
from incident_intelligence.persistence.diagnosis_repository import DiagnosisToolReceiptRecord
from incident_intelligence.services.diagnosis_capabilities import DiagnosisCapabilityIssuer
from incident_intelligence.services.diagnosis_tools import (
    DiagnosisEvidenceScopeDenied,
    DiagnosisToolService,
)

NOW = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
RUN_ID = f"drun_{'1' * 32}"
OUTSIDE_EVIDENCE_ID = f"evitem_{'2' * 32}"


def test_evidence_tool_rejects_item_outside_snapshot_and_audits_denial() -> None:
    receipts: list[DiagnosisToolReceiptRecord] = []
    issuer = DiagnosisCapabilityIssuer(
        hmac_secret="test-only-hmac-secret",
        clock=lambda: NOW,
        nonce_factory=lambda: "nonce-for-test",
    )
    service = DiagnosisToolService(
        uow_factory=lambda: _FakeUnitOfWork(receipts),
        capability_issuer=issuer,
        clock=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}_{'3' * 32}",
    )
    token = issuer.issue(RUN_ID, expires_at=NOW + timedelta(minutes=5))

    with pytest.raises(DiagnosisEvidenceScopeDenied, match="diagnosis_evidence_scope_denied"):
        service.get_evidence_detail(
            RUN_ID,
            capability_token=token,
            evidence_item_id=OUTSIDE_EVIDENCE_ID,
        )

    assert len(receipts) == 1
    assert receipts[0].error_code == "diagnosis_evidence_scope_denied"
    assert receipts[0].request_hash != token


def test_snapshot_tool_returns_only_a_bounded_snapshot_projection() -> None:
    receipts: list[DiagnosisToolReceiptRecord] = []
    issuer = DiagnosisCapabilityIssuer(
        hmac_secret="test-only-hmac-secret",
        clock=lambda: NOW,
        nonce_factory=lambda: "nonce-for-test",
    )
    service = DiagnosisToolService(
        uow_factory=lambda: _FakeUnitOfWork(receipts),
        capability_issuer=issuer,
        clock=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}_{'7' * 32}",
    )
    token = issuer.issue(RUN_ID, expires_at=NOW + timedelta(minutes=5))

    result = service.get_diagnosis_snapshot(RUN_ID, capability_token=token)

    assert result.incident_id == f"inc_{'4' * 32}"
    assert result.evidence_summaries[0].alias == "E1"
    assert result.evidence_summaries[0].display_name == "数据库行锁等待"
    assert result.evidence_summaries[0].fault_summary == {"current": 1}
    assert result.evidence_summaries[0].interpretation == "数据库行锁等待升高。"
    serialized = result.model_dump_json()
    assert f"evitem_{'6' * 32}" not in serialized
    assert "content_hash" not in serialized
    assert receipts[0].tool_key == "get_diagnosis_snapshot"
    assert receipts[0].result_hash is not None


def test_evidence_tool_returns_only_the_referenced_evidence_item() -> None:
    receipts: list[DiagnosisToolReceiptRecord] = []
    issuer = DiagnosisCapabilityIssuer(
        hmac_secret="test-only-hmac-secret",
        clock=lambda: NOW,
        nonce_factory=lambda: "nonce-for-test",
    )
    service = DiagnosisToolService(
        uow_factory=lambda: _FakeUnitOfWork(receipts),
        capability_issuer=issuer,
        clock=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}_{'8' * 32}",
    )
    token = issuer.issue(RUN_ID, expires_at=NOW + timedelta(minutes=5))

    result = service.get_evidence_detail(
        RUN_ID,
        capability_token=token,
        evidence_item_id=f"evitem_{'6' * 32}",
    )

    assert result.id == f"evitem_{'6' * 32}"
    assert result.interpretation == "数据库行锁等待升高。"
    assert receipts[0].result_hash is not None


class _FakeUnitOfWork:
    def __init__(self, receipts: list[DiagnosisToolReceiptRecord]) -> None:
        self.diagnosis_runs = _FakeDiagnosisRuns()
        self.evidence_runs = _FakeEvidenceRuns()
        self.diagnosis_tool_receipts = _FakeToolReceipts(receipts)

    def __enter__(self) -> _FakeUnitOfWork:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def commit(self) -> None:
        return None


class _FakeDiagnosisRuns:
    def get_snapshot(self, run_id: str) -> DiagnosisSnapshot | None:
        if run_id != RUN_ID:
            return None
        return DiagnosisSnapshot(
            diagnosis_run_id=RUN_ID,
            incident_id=f"inc_{'4' * 32}",
            evidence_run_id=f"evr_{'5' * 32}",
            environment="testing",
            service_name="checkout",
            alert_names=("HighErrorRate",),
            evidence_references=(
                DiagnosisReference(
                    kind="EVIDENCE",
                    target_id=f"evitem_{'6' * 32}",
                    content_hash="a" * 64,
                ),
            ),
            created_at=NOW,
        )


class _FakeToolReceipts:
    def __init__(self, receipts: list[DiagnosisToolReceiptRecord]) -> None:
        self._receipts = receipts

    def list_for_run(self, diagnosis_run_id: str) -> tuple[DiagnosisToolReceiptRecord, ...]:
        return tuple(
            receipt for receipt in self._receipts if receipt.diagnosis_run_id == diagnosis_run_id
        )

    def insert(self, receipt: DiagnosisToolReceiptRecord) -> None:
        self._receipts.append(receipt)


class _FakeEvidenceRuns:
    def list_items(self, evidence_run_id: str, *, limit: int) -> tuple[EvidenceItem, ...]:
        item = self.get_item(f"evitem_{'6' * 32}")
        return (item,) if evidence_run_id == f"evr_{'5' * 32}" and item is not None else ()

    def get_item(self, evidence_item_id: str) -> EvidenceItem | None:
        if evidence_item_id != f"evitem_{'6' * 32}":
            return None
        return EvidenceItem(
            id=evidence_item_id,
            evidence_run_id=f"evr_{'5' * 32}",
            evidence_key="mysql.row_lock_wait",
            display_name="数据库行锁等待",
            source_type="PROMETHEUS",
            state="SUCCEEDED",
            package_id="prometheus.mysql",
            package_version=1,
            template_id="row-lock-wait",
            template_version=1,
            evidence_type="METRIC_COMPARISON",
            query_started_at=NOW,
            query_ended_at=NOW,
            baseline_summary={"current": 0},
            fault_summary={"current": 1},
            interpretation="数据库行锁等待升高。",
            normalized_result={},
            created_at=NOW,
        )
