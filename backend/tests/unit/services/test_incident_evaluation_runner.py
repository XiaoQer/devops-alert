from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from incident_intelligence.persistence.incident_repository import (
    IncidentEvaluationJobRecord,
)
from incident_intelligence.services.incident_evaluation_runner import (
    IncidentEvaluationRunner,
)

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def test_runner_retries_transient_failure_then_fails_at_attempt_limit() -> None:
    clock = MutableClock(NOW)
    repository = FakeJobRepository(_job())
    runner = IncidentEvaluationRunner(
        uow_factory=lambda: FakeUnitOfWork(repository),
        processor=AlwaysFailingProcessor(),
        clock=clock,
        owner="worker-1",
        lease_seconds=30,
        max_attempts=2,
        retry_base_seconds=5,
    )

    first = runner.run_once(limit=20)

    assert (first.leased, first.succeeded, first.retried, first.failed) == (1, 0, 1, 0)
    assert repository.record.state == "PENDING"
    assert repository.record.attempt_count == 1
    assert repository.record.available_at == NOW + timedelta(seconds=5)
    assert repository.record.last_error_code == "runtimeerror"

    clock.current = NOW + timedelta(seconds=5)
    second = runner.run_once(limit=20)

    assert (second.leased, second.succeeded, second.retried, second.failed) == (1, 0, 0, 1)
    assert repository.record.state == "FAILED"
    assert repository.record.attempt_count == 2
    assert repository.record.last_error_code == "runtimeerror"
    assert repository.record.lease_owner is None


def test_runner_reports_successful_processor_result() -> None:
    repository = FakeJobRepository(_job())
    runner = IncidentEvaluationRunner(
        uow_factory=lambda: FakeUnitOfWork(repository),
        processor=SuccessfulProcessor(repository),
        clock=lambda: NOW,
        owner="worker-1",
        lease_seconds=30,
        max_attempts=3,
    )

    result = runner.run_once(limit=1)

    assert (result.leased, result.succeeded, result.retried, result.failed) == (1, 1, 0, 0)
    assert repository.record.state == "SUCCEEDED"


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class AlwaysFailingProcessor:
    def process(self, job_id: str) -> None:
        del job_id
        raise RuntimeError("temporary database failure")


class SuccessfulProcessor:
    def __init__(self, repository: FakeJobRepository) -> None:
        self._repository = repository

    def process(self, job_id: str) -> None:
        assert job_id == self._repository.record.id
        self._repository.record = replace(
            self._repository.record,
            state="SUCCEEDED",
            lease_owner=None,
            lease_expires_at=None,
        )


class FakeUnitOfWork:
    def __init__(self, repository: FakeJobRepository) -> None:
        self.incident_evaluation_jobs = repository

    def __enter__(self) -> FakeUnitOfWork:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def commit(self) -> None:
        return None


class FakeJobRepository:
    def __init__(self, record: IncidentEvaluationJobRecord) -> None:
        self.record = record

    def lease_due(
        self,
        *,
        owner: str,
        now: datetime,
        lease_until: datetime,
        limit: int,
    ) -> tuple[IncidentEvaluationJobRecord, ...]:
        if limit < 1 or self.record.state != "PENDING" or self.record.available_at > now:
            return ()
        self.record = replace(
            self.record,
            state="LEASED",
            attempt_count=self.record.attempt_count + 1,
            lease_owner=owner,
            lease_expires_at=lease_until,
            updated_at=now,
        )
        return (self.record,)

    def retry(
        self,
        job_id: str,
        *,
        owner: str,
        error_code: str,
        available_at: datetime,
        now: datetime,
    ) -> bool:
        if not self._owned(job_id, owner):
            return False
        self.record = replace(
            self.record,
            state="PENDING",
            available_at=available_at,
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=error_code,
            updated_at=now,
        )
        return True

    def fail(
        self,
        job_id: str,
        *,
        owner: str,
        error_code: str,
        now: datetime,
    ) -> bool:
        if not self._owned(job_id, owner):
            return False
        self.record = replace(
            self.record,
            state="FAILED",
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=error_code,
            updated_at=now,
        )
        return True

    def _owned(self, job_id: str, owner: str) -> bool:
        return (
            self.record.id == job_id
            and self.record.state == "LEASED"
            and self.record.lease_owner == owner
        )


def _job() -> IncidentEvaluationJobRecord:
    return IncidentEvaluationJobRecord(
        id="iej_11111111111111111111111111111111",
        alert_id="alt_22222222222222222222222222222222",
        alert_version=1,
        state="PENDING",
        attempt_count=0,
        available_at=NOW,
        lease_owner=None,
        lease_expires_at=None,
        last_error_code=None,
        outcome=None,
        reason_codes=(),
        incident_ids=(),
        created_at=NOW,
        updated_at=NOW,
    )
