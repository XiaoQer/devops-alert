from __future__ import annotations

from datetime import UTC, datetime

from incident_intelligence.services.diagnosis_runner import DiagnosisRunner

NOW = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)


def test_runner_processes_each_due_diagnosis_task_once() -> None:
    processor = _FakeProcessor()
    runner = DiagnosisRunner(
        uow_factory=lambda: _FakeUnitOfWork(),
        processor=processor,
        clock=lambda: NOW,
    )

    result = runner.run_once(limit=5)

    assert processor.task_ids == ("dtask_a", "dtask_b")
    assert result.scanned == 2
    assert result.report_ready == 1
    assert result.review_required == 1


class _FakeProcessor:
    def __init__(self) -> None:
        self.task_ids: tuple[str, ...] = ()

    def process(self, task_id: str) -> str:
        self.task_ids += (task_id,)
        return "REPORT_READY" if task_id == "dtask_a" else "REVIEW_REQUIRED"


class _FakeUnitOfWork:
    diagnosis_tasks: _FakeUnitOfWork

    def __init__(self) -> None:
        self.diagnosis_tasks = self

    def __enter__(self) -> _FakeUnitOfWork:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def commit(self) -> None:
        return None

    def requeue_expired(self, *, now: datetime) -> int:
        del now
        return 0

    def list_due(self, *, now: datetime, limit: int) -> tuple[str, ...]:
        del now, limit
        return ("dtask_a", "dtask_b")
