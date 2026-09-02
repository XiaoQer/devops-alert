from __future__ import annotations

from datetime import UTC, datetime

from incident_intelligence.services.evidence_collection import EvidenceCollectionResult
from incident_intelligence.services.evidence_collection_runner import EvidenceCollectionRunner

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def test_runner_processes_due_tasks_and_counts_partial_as_completed() -> None:
    repository = _FakeTaskRepository(("evtask_11111111111111111111111111111111",))
    processor = _FakeProcessor()
    runner = EvidenceCollectionRunner(
        uow_factory=lambda: _FakeUnitOfWork(repository),
        processor=processor,
        clock=lambda: NOW,
    )

    result = runner.run_once(limit=10)

    assert result.scanned == 1
    assert result.completed == 1
    assert result.retried == 0
    assert processor.processed == ["evtask_11111111111111111111111111111111"]


def test_runner_counts_retry_and_isolates_unexpected_task_failure() -> None:
    repository = _FakeTaskRepository(("retry", "fail"))
    runner = EvidenceCollectionRunner(
        uow_factory=lambda: _FakeUnitOfWork(repository),
        processor=_MixedProcessor(),
        clock=lambda: NOW,
    )

    result = runner.run_once(limit=10)

    assert (result.scanned, result.completed, result.retried, result.failed) == (2, 0, 1, 1)


class _FakeProcessor:
    def __init__(self) -> None:
        self.processed: list[str] = []

    def process(self, task_id: str) -> EvidenceCollectionResult:
        self.processed.append(task_id)
        return EvidenceCollectionResult(
            task_id=task_id,
            evidence_run_id="evr_22222222222222222222222222222222",
            outcome="PARTIAL",
        )


class _MixedProcessor:
    def process(self, task_id: str) -> EvidenceCollectionResult:
        if task_id == "fail":
            raise RuntimeError("safe test failure")
        return EvidenceCollectionResult(
            task_id=task_id,
            evidence_run_id="evr_22222222222222222222222222222222",
            outcome="RETRY_SCHEDULED",
        )


class _FakeTaskRepository:
    def __init__(self, due: tuple[str, ...]) -> None:
        self.due = due

    def list_due(self, *, now: datetime, limit: int) -> tuple[str, ...]:
        assert now == NOW
        return self.due[:limit]

    def requeue_expired(self, *, now: datetime) -> int:
        assert now == NOW
        return 0


class _FakeUnitOfWork:
    def __init__(self, repository: _FakeTaskRepository) -> None:
        self.evidence_tasks = repository

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def commit(self) -> None:
        return None
