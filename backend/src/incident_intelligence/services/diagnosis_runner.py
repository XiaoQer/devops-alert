from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.persistence.diagnosis_repository import DiagnosisTaskRepository
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.diagnosis_execution import DiagnosisExecutionService


class DiagnosisRunnerBatchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scanned: int = Field(ge=0)
    report_ready: int = Field(ge=0)
    review_required: int = Field(ge=0)
    failed: int = Field(ge=0)


class DiagnosisRunner:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        processor: DiagnosisExecutionService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._processor = processor
        self._clock = clock or (lambda: datetime.now(UTC))

    def run_once(self, *, limit: int = 5) -> DiagnosisRunnerBatchResult:
        now = self._clock().astimezone(UTC)
        with self._uow_factory() as uow:
            tasks = _tasks(uow)
            tasks.requeue_expired(now=now)
            due = tasks.list_due(now=now, limit=limit)
            uow.commit()

        report_ready = 0
        review_required = 0
        failed = 0
        for task_id in due:
            outcome = self._processor.process(task_id)
            report_ready += int(outcome == "REPORT_READY")
            review_required += int(outcome == "REVIEW_REQUIRED")
            failed += int(outcome == "FAILED")
        return DiagnosisRunnerBatchResult(
            scanned=len(due),
            report_ready=report_ready,
            review_required=review_required,
            failed=failed,
        )


def _tasks(uow: SqlAlchemyUnitOfWork) -> DiagnosisTaskRepository:
    if uow.diagnosis_tasks is None:
        raise RuntimeError("诊断任务仓储尚未初始化")
    return uow.diagnosis_tasks
