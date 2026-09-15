from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from incident_intelligence.domain.diagnosis import (
    transition_diagnosis_run,
    validate_candidate_report,
)
from incident_intelligence.persistence.diagnosis_repository import (
    DiagnosisReportRepository,
    DiagnosisRunRepository,
    DiagnosisTaskRepository,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.diagnosis_capabilities import DiagnosisCapabilityIssuer

DiagnosisExecutionOutcome = Literal[
    "REPORT_READY",
    "REVIEW_REQUIRED",
    "FAILED",
    "NOT_CLAIMED",
]


class DiagnosisWorkflow(Protocol):
    def run_workflow(
        self,
        diagnosis_run_id: str,
        *,
        capability_token: str,
    ) -> dict[str, object]: ...


class DiagnosisExecutionService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        workflow: DiagnosisWorkflow,
        capability_issuer: DiagnosisCapabilityIssuer,
        owner: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._workflow = workflow
        self._capability_issuer = capability_issuer
        self._owner = owner
        self._clock = clock or (lambda: datetime.now(UTC))

    def process(self, task_id: str) -> DiagnosisExecutionOutcome:
        now = self._clock().astimezone(UTC)
        with self._uow_factory() as uow:
            task = _tasks(uow).get(task_id)
            if task is None or not _tasks(uow).claim_due(
                task_id,
                owner=self._owner,
                now=now,
                lease_until=now + timedelta(seconds=90),
            ):
                return "NOT_CLAIMED"
            run = _runs(uow).get(task.diagnosis_run_id)
            if run is None:
                _tasks(uow).fail(
                    task_id,
                    owner=self._owner,
                    error_code="diagnosis_run_not_found",
                    now=now,
                )
                uow.commit()
                return "FAILED"
            running = transition_diagnosis_run(run, state="RUNNING", now=now)
            if not _runs(uow).update(running, expected_version=run.version):
                return "NOT_CLAIMED"
            uow.commit()

        capability_token = self._capability_issuer.issue(
            running.id,
            expires_at=now + timedelta(minutes=5),
        )
        try:
            candidate = self._workflow.run_workflow(
                running.id,
                capability_token=capability_token,
            )
        except Exception:
            return self._fail(task_id, running.id, now)

        with self._uow_factory() as uow:
            current_run = _runs(uow).get(running.id)
            snapshot = _runs(uow).get_snapshot(running.id)
            if current_run is None or snapshot is None or current_run.state != "RUNNING":
                return "NOT_CLAIMED"
            validation = validate_candidate_report(snapshot, candidate)
            if validation.accepted and validation.report is not None:
                _reports(uow).insert(running.id, validation.report, created_at=now)
                final_state: Literal["REPORT_READY", "REVIEW_REQUIRED"] = "REPORT_READY"
            else:
                final_state = "REVIEW_REQUIRED"
            completed = transition_diagnosis_run(current_run, state=final_state, now=now)
            if not _runs(uow).update(completed, expected_version=current_run.version):
                return "NOT_CLAIMED"
            _tasks(uow).complete(task_id, owner=self._owner, now=now)
            uow.commit()
            return final_state

    def _fail(
        self,
        task_id: str,
        diagnosis_run_id: str,
        now: datetime,
    ) -> DiagnosisExecutionOutcome:
        with self._uow_factory() as uow:
            run = _runs(uow).get(diagnosis_run_id)
            if run is not None and run.state == "RUNNING":
                failed = transition_diagnosis_run(run, state="FAILED", now=now)
                _runs(uow).update(failed, expected_version=run.version)
            _tasks(uow).fail(
                task_id,
                owner=self._owner,
                error_code="diagnosis_workflow_failed",
                now=now,
            )
            uow.commit()
        return "FAILED"


def _runs(uow: SqlAlchemyUnitOfWork) -> DiagnosisRunRepository:
    if uow.diagnosis_runs is None:
        raise RuntimeError("诊断运行仓储尚未初始化")
    return uow.diagnosis_runs


def _tasks(uow: SqlAlchemyUnitOfWork) -> DiagnosisTaskRepository:
    if uow.diagnosis_tasks is None:
        raise RuntimeError("诊断任务仓储尚未初始化")
    return uow.diagnosis_tasks


def _reports(uow: SqlAlchemyUnitOfWork) -> DiagnosisReportRepository:
    if uow.diagnosis_reports is None:
        raise RuntimeError("诊断报告仓储尚未初始化")
    return uow.diagnosis_reports
