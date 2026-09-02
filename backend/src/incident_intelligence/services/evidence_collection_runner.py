from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.persistence.evidence_repository import EvidenceTaskRepository
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.evidence_collection import EvidenceCollectionService


class EvidenceRunnerBatchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scanned: int = Field(ge=0)
    completed: int = Field(ge=0)
    retried: int = Field(ge=0)
    failed: int = Field(ge=0)


class EvidenceCollectionRunner:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        processor: EvidenceCollectionService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._processor = processor
        self._clock = clock or (lambda: datetime.now(UTC))

    def run_once(self, *, limit: int = 5) -> EvidenceRunnerBatchResult:
        now = self._clock().astimezone(UTC)
        with self._uow_factory() as uow:
            repository = _tasks(uow)
            repository.requeue_expired(now=now)
            due = repository.list_due(now=now, limit=limit)
            uow.commit()

        completed = 0
        retried = 0
        failed = 0
        for task_id in due:
            try:
                result = self._processor.process(task_id)
            except Exception:
                failed += 1
                continue
            if result.outcome == "RETRY_SCHEDULED":
                retried += 1
            else:
                completed += 1
        return EvidenceRunnerBatchResult(
            scanned=len(due),
            completed=completed,
            retried=retried,
            failed=failed,
        )


def _tasks(uow: SqlAlchemyUnitOfWork) -> EvidenceTaskRepository:
    if uow.evidence_tasks is None:
        raise RuntimeError("取证任务仓储尚未初始化")
    return uow.evidence_tasks
