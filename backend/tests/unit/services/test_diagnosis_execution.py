from __future__ import annotations

from datetime import UTC, datetime

from incident_intelligence.adapters.dify import DifyRetryableError
from incident_intelligence.domain.diagnosis import (
    DiagnosisReference,
    DiagnosisRun,
    DiagnosisSnapshot,
)
from incident_intelligence.persistence.diagnosis_repository import DiagnosisTaskRecord
from incident_intelligence.services.diagnosis_capabilities import DiagnosisCapabilityIssuer
from incident_intelligence.services.diagnosis_execution import DiagnosisExecutionService

NOW = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
RUN_ID = f"drun_{'1' * 32}"
TASK_ID = f"dtask_{'2' * 32}"
EVIDENCE_ID = f"evitem_{'3' * 32}"


def test_execution_publishes_a_valid_demo_draft_only_after_platform_validation() -> None:
    uow = _FakeUnitOfWork()
    service = DiagnosisExecutionService(
        uow_factory=lambda: uow,
        workflow=_ValidWorkflow(),
        capability_issuer=DiagnosisCapabilityIssuer(
            hmac_secret="test-only-hmac-secret",
            clock=lambda: NOW,
            nonce_factory=lambda: "nonce-for-test",
        ),
        owner="demo-worker",
        clock=lambda: NOW,
    )

    outcome = service.process(TASK_ID)

    assert outcome == "REPORT_READY"
    assert uow.run.state == "REPORT_READY"
    assert uow.report is not None
    assert uow.task_completed is True


def test_temporary_dify_failure_is_rescheduled_without_failing_diagnosis_run() -> None:
    uow = _FakeUnitOfWork()
    service = DiagnosisExecutionService(
        uow_factory=lambda: uow,
        workflow=_RetryableWorkflow(),
        capability_issuer=DiagnosisCapabilityIssuer(
            hmac_secret="test-only-hmac-secret",
            clock=lambda: NOW,
            nonce_factory=lambda: "nonce-for-test",
        ),
        owner="dify-worker",
        clock=lambda: NOW,
    )

    outcome = service.process(TASK_ID)

    assert outcome == "RETRY_SCHEDULED"
    assert uow.run.state == "RUNNING"
    assert uow.task_rescheduled == ("dify_rate_limited", NOW.replace(second=5))


class _FakeUnitOfWork:
    def __init__(self) -> None:
        self.run = DiagnosisRun(
            id=RUN_ID,
            incident_id=f"inc_{'4' * 32}",
            evidence_run_id=f"evr_{'5' * 32}",
            state="QUEUED",
            requested_by="operator",
            created_at=NOW,
        )
        self.snapshot = DiagnosisSnapshot(
            diagnosis_run_id=RUN_ID,
            incident_id=self.run.incident_id,
            evidence_run_id=self.run.evidence_run_id,
            environment="testing",
            service_name="checkout",
            alert_names=("HighErrorRate",),
            evidence_references=(
                DiagnosisReference(
                    kind="EVIDENCE",
                    target_id=EVIDENCE_ID,
                    content_hash="a" * 64,
                ),
            ),
            created_at=NOW,
        )
        self.diagnosis_tasks = self
        self.diagnosis_runs = self
        self.diagnosis_reports = self
        self.task_completed = False
        self.task_rescheduled: tuple[str, datetime] | None = None
        self.report: object | None = None

    def __enter__(self) -> _FakeUnitOfWork:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def commit(self) -> None:
        return None

    def get(self, identifier: str) -> object | None:
        if identifier == TASK_ID:
            return DiagnosisTaskRecord(
                id=TASK_ID,
                diagnosis_run_id=RUN_ID,
                state="PENDING",
                attempt_count=0,
                next_attempt_at=NOW,
                lease_owner=None,
                lease_until=None,
                last_error_code=None,
                created_at=NOW,
                updated_at=NOW,
            )
        if identifier == RUN_ID:
            return self.run
        return None

    def claim_due(self, *args: object, **kwargs: object) -> bool:
        return True

    def get_snapshot(self, run_id: str) -> DiagnosisSnapshot | None:
        return self.snapshot if run_id == RUN_ID else None

    def update(self, run: DiagnosisRun, *, expected_version: int) -> bool:
        del expected_version
        self.run = run
        return True

    def insert(self, diagnosis_run_id: str, report: object, *, created_at: datetime) -> None:
        del diagnosis_run_id, created_at
        self.report = report

    def complete(self, *args: object, **kwargs: object) -> bool:
        self.task_completed = True
        return True

    def reschedule(
        self,
        task_id: str,
        *,
        owner: str,
        error_code: str,
        next_attempt_at: datetime,
        now: datetime,
    ) -> bool:
        del task_id, owner, now
        self.task_rescheduled = (error_code, next_attempt_at)
        return True


class _ValidWorkflow:
    def run_workflow(self, diagnosis_run_id: str, *, capability_token: str) -> dict[str, object]:
        assert diagnosis_run_id == RUN_ID
        assert capability_token
        return {
            "confirmed_facts": [{"text": "数据库行锁等待升高。", "reference_ids": [EVIDENCE_ID]}],
            "hypotheses": [],
            "references": [
                {"kind": "EVIDENCE", "target_id": EVIDENCE_ID, "content_hash": "a" * 64}
            ],
            "unknowns": [],
            "suggested_human_actions": ["请人工核对数据库长事务。"],
        }


class _RetryableWorkflow:
    def run_workflow(self, diagnosis_run_id: str, *, capability_token: str) -> dict[str, object]:
        del diagnosis_run_id, capability_token
        raise DifyRetryableError("dify_rate_limited")
