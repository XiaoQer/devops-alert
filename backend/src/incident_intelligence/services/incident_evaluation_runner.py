from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.persistence.incident_repository import (
    IncidentEvaluationJobRepository,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.incident_evaluation import IncidentEvaluationService


class RunnerBatchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    leased: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    retried: int = Field(ge=0)
    failed: int = Field(ge=0)


class IncidentEvaluationRunner:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        processor: IncidentEvaluationService,
        clock: Callable[[], datetime] | None = None,
        owner: str = "incident-evaluation",
        lease_seconds: int = 60,
        max_attempts: int = 5,
        retry_base_seconds: int = 5,
    ) -> None:
        self._uow_factory = uow_factory
        self._processor = processor
        self._clock = clock or (lambda: datetime.now(UTC))
        self._owner = owner
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds

    def run_once(self, *, limit: int = 20) -> RunnerBatchResult:
        now = self._clock().astimezone(UTC)
        with self._uow_factory() as uow:
            leased = _jobs(uow).lease_due(
                owner=self._owner,
                now=now,
                lease_until=now + timedelta(seconds=self._lease_seconds),
                limit=limit,
            )
            uow.commit()

        succeeded = 0
        retried = 0
        failed = 0
        for job in leased:
            try:
                self._processor.process(job.id)
                succeeded += 1
            except Exception as error:
                error_code = type(error).__name__.casefold()[:64]
                transition_now = self._clock().astimezone(UTC)
                with self._uow_factory() as uow:
                    repository = _jobs(uow)
                    if job.attempt_count >= self._max_attempts:
                        changed = repository.fail(
                            job.id,
                            owner=self._owner,
                            error_code=error_code,
                            now=transition_now,
                        )
                        failed += int(changed)
                    else:
                        delay = min(
                            300,
                            self._retry_base_seconds
                            * (2 ** max(0, job.attempt_count - 1)),
                        )
                        changed = repository.retry(
                            job.id,
                            owner=self._owner,
                            error_code=error_code,
                            available_at=transition_now + timedelta(seconds=delay),
                            now=transition_now,
                        )
                        retried += int(changed)
                    if not changed:
                        raise RuntimeError("incident_evaluation_job_lease_lost") from error
                    uow.commit()

        return RunnerBatchResult(
            leased=len(leased),
            succeeded=succeeded,
            retried=retried,
            failed=failed,
        )


def _jobs(uow: SqlAlchemyUnitOfWork) -> IncidentEvaluationJobRepository:
    if uow.incident_evaluation_jobs is None:
        raise RuntimeError("工作单元没有可用 Incident 评估任务仓储")
    return uow.incident_evaluation_jobs
