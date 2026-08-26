from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from pydantic import SecretStr

from incident_intelligence.services.alert_grouping_jobs import AlertGroupingJobLease
from incident_intelligence.services.alert_grouping_runner import AlertGroupingRunner
from incident_intelligence.settings import Settings

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)


class FakeJobs:
    def __init__(self) -> None:
        self.claim_calls = 0
        self.failed: list[tuple[str, str]] = []

    def claim_batch(
        self,
        lease_owner: str,
        now: datetime,
        *,
        limit: int,
        lease_seconds: int,
    ) -> tuple[AlertGroupingJobLease, ...]:
        del now, limit, lease_seconds
        self.claim_calls += 1
        return tuple(
            AlertGroupingJobLease(
                id=f"agj_{number:032x}",
                alert_id=f"alt_{number:032x}",
                alert_cycle=1,
                alert_version=1,
                state="LEASED",
                attempts=1,
                available_at=NOW,
                lease_owner=lease_owner,
                lease_expires_at=NOW + timedelta(seconds=30),
                last_error_code=None,
            )
            for number in (1, 2)
        )

    def fail(
        self,
        job_id: str,
        lease_owner: str,
        error_code: str,
        now: datetime,
    ) -> str:
        del lease_owner, now
        self.failed.append((job_id, error_code))
        return "PENDING"


class FailsFirstProcessor:
    def __init__(self) -> None:
        self.processed: list[str] = []

    def process(self, lease: AlertGroupingJobLease) -> object:
        self.processed.append(lease.id)
        if lease.id.endswith("1"):
            raise RuntimeError("sensitive database detail")
        return object()


def settings() -> Settings:
    return Settings(
        database_url="mysql+pymysql://test-client@127.0.0.1/unused",
        api_token=SecretStr("manual-token-value"),
        alertmanager_token=SecretStr("alertmanager-token-value"),
        cloudevents_token=SecretStr("cloudevents-token-value"),
        correlation_runner_enabled=False,
        alert_grouping_runner_enabled=True,
        alert_grouping_batch_size=2,
    )


def test_runner_isolates_one_job_failure_and_uses_fixed_error_code() -> None:
    jobs = FakeJobs()
    processor = FailsFirstProcessor()
    runner = AlertGroupingRunner(
        job_service=jobs,
        processor=processor,
        settings=settings(),
        clock=lambda: NOW,
    )

    processed = asyncio.run(runner.run_once())

    assert processed == 2
    assert len(processor.processed) == 2
    assert jobs.failed == [(f"agj_{1:032x}", "alert_grouping_processing_failed")]
    assert runner.last_cycle_error_code is None


def test_runner_stop_prevents_polling() -> None:
    jobs = FakeJobs()
    runner = AlertGroupingRunner(
        job_service=jobs,
        processor=FailsFirstProcessor(),
        settings=settings(),
        clock=lambda: NOW,
    )

    async def exercise() -> None:
        await runner.stop()
        await runner.run_forever()

    asyncio.run(exercise())

    assert jobs.claim_calls == 0
