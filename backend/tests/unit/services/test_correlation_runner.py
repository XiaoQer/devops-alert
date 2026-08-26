from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from pydantic import SecretStr

from incident_intelligence.services.alert_group_correlation_jobs import (
    AlertGroupCorrelationLease,
)
from incident_intelligence.services.correlation_jobs import CorrelationJobLease
from incident_intelligence.services.correlation_runner import (
    AlertGroupCorrelationRunner,
    CorrelationRunner,
)
from incident_intelligence.settings import Settings

NOW = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)


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
    ) -> tuple[CorrelationJobLease, ...]:
        del now, limit, lease_seconds
        self.claim_calls += 1
        return tuple(
            CorrelationJobLease(
                id=f"cjob_{number:032x}",
                alert_id=f"alt_{number:032x}",
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

    def process(self, lease: CorrelationJobLease) -> object:
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
        correlation_runner_enabled=True,
        correlation_batch_size=2,
    )


def test_runner_isolates_single_job_failure_and_uses_fixed_error_code() -> None:
    jobs = FakeJobs()
    processor = FailsFirstProcessor()
    runner = CorrelationRunner(
        job_service=jobs,
        processor=processor,
        settings=settings(),
        clock=lambda: NOW,
    )

    processed = asyncio.run(runner.run_once())

    assert processed == 2
    assert len(processor.processed) == 2
    assert jobs.failed == [(f"cjob_{1:032x}", "correlation_processing_failed")]
    assert runner.last_cycle_error_code is None


def test_runner_cycle_failure_is_bounded_and_stop_prevents_polling() -> None:
    jobs = FakeJobs()
    processor = FailsFirstProcessor()
    runner = CorrelationRunner(
        job_service=jobs,
        processor=processor,
        settings=settings(),
        clock=lambda: NOW,
    )

    async def exercise() -> None:
        await runner.stop()
        await runner.run_forever()

    asyncio.run(exercise())

    assert jobs.claim_calls == 0


class FakeGroupJobs:
    def __init__(self) -> None:
        self.claimed_at: datetime | None = None
        self.failed: list[tuple[str, str, datetime]] = []

    def claim_batch(
        self,
        lease_owner: str,
        now: datetime,
        *,
        limit: int,
        lease_seconds: int,
    ) -> tuple[AlertGroupCorrelationLease, ...]:
        del limit, lease_seconds
        self.claimed_at = now
        return (
            AlertGroupCorrelationLease(
                id=f"gcj_{1:032x}",
                alert_group_id=f"agr_{1:032x}",
                target_group_version=2,
                state="LEASED",
                attempts=1,
                available_at=NOW,
                lease_owner=lease_owner,
                lease_expires_at=NOW + timedelta(seconds=30),
                last_error_code=None,
            ),
        )

    def fail(
        self,
        job_id: str,
        lease_owner: str,
        error_code: str,
        now: datetime,
    ) -> str:
        del lease_owner
        self.failed.append((job_id, error_code, now))
        return "PENDING"


class FailingGroupProcessor:
    def process(self, lease: AlertGroupCorrelationLease) -> object:
        del lease
        raise RuntimeError("不得外泄的数据库详情")


def test_group_runner_uses_injected_clock_and_fixed_safe_error_code() -> None:
    jobs = FakeGroupJobs()
    runner = AlertGroupCorrelationRunner(
        job_service=jobs,
        processor=FailingGroupProcessor(),
        settings=settings(),
        clock=lambda: NOW,
    )

    processed = asyncio.run(runner.run_once())

    assert processed == 1
    assert jobs.claimed_at == NOW
    assert jobs.failed == [(f"gcj_{1:032x}", "group_correlation_processing_failed", NOW)]
    assert runner.last_cycle_error_code is None
