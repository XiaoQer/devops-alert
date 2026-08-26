from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.signal_intake import SignalCommand
from incident_intelligence.persistence.models import AlertGroupingJobRow
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_grouping_jobs import (
    AlertGroupingJobNotRetryable,
    AlertGroupingJobService,
)
from incident_intelligence.services.signal_intake import SignalIntakeService

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)


@pytest.fixture
def session_factory(migrated_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def job_service(session_factory: sessionmaker[Session]) -> AlertGroupingJobService:
    return AlertGroupingJobService(uow_factory=partial(SqlAlchemyUnitOfWork, session_factory))


@pytest.fixture
def queued_job_id(session_factory: sessionmaker[Session]) -> str:
    intake = SignalIntakeService(
        uow_factory=partial(SqlAlchemyUnitOfWork, session_factory),
        clock=lambda: NOW,
    )
    intake.submit_batch(
        [
            SignalCommand(
                alert_source_id="src_00000000000000000000000000000002",
                source="alertmanager",
                source_instance="1" * 64,
                source_event_id="2" * 64,
                source_alert_key="payment-high-error-rate",
                event_type="alert.firing",
                event_at=NOW,
                episode_started_at=NOW,
                title="支付接口错误率升高",
                summary="支付接口错误率超过阈值",
                severity="high",
                service="payment-api",
                environment="production",
                facts={"symptom": "high-error-rate"},
            )
        ],
        "alertmanager-adapter",
        "req-1",
    )
    with session_factory() as session:
        job_id = session.scalar(select(AlertGroupingJobRow.id))
    assert job_id is not None
    return job_id


def test_claim_uses_lease_and_expired_job_can_be_reclaimed(
    job_service: AlertGroupingJobService,
    queued_job_id: str,
) -> None:
    first = job_service.claim_batch("runner-a", NOW, limit=1, lease_seconds=30)
    assert first[0].id == queued_job_id
    assert first[0].attempts == 1
    assert first[0].alert_cycle == 1
    assert job_service.claim_batch("runner-b", NOW, limit=1, lease_seconds=30) == ()

    reclaimed = job_service.claim_batch(
        "runner-b", NOW + timedelta(seconds=31), limit=1, lease_seconds=30
    )
    assert reclaimed[0].id == queued_job_id
    assert reclaimed[0].attempts == 2


def test_fifth_failure_becomes_failed_and_manual_retry_resets_job(
    job_service: AlertGroupingJobService,
    queued_job_id: str,
) -> None:
    now = NOW
    for attempt in range(1, 6):
        lease = job_service.claim_batch("runner-a", now, limit=1, lease_seconds=30)[0]
        state = job_service.fail(lease.id, "runner-a", "alert_grouping_processing_failed", now)
        assert state == ("FAILED" if attempt == 5 else "PENDING")
        now += timedelta(minutes=10)

    retried = job_service.retry_failed(queued_job_id, now)
    assert retried.state == "PENDING"
    assert retried.attempts == 0
    with pytest.raises(AlertGroupingJobNotRetryable):
        job_service.retry_failed(queued_job_id, now)


def test_concurrent_claim_never_returns_same_job_twice(
    job_service: AlertGroupingJobService,
    queued_job_id: str,
) -> None:
    ready = Barrier(2)

    def claim(owner: str) -> tuple[str, ...]:
        ready.wait(timeout=5)
        return tuple(
            lease.id for lease in job_service.claim_batch(owner, NOW, limit=1, lease_seconds=30)
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ["runner-a", "runner-b"]))

    assert sorted(len(result) for result in results) == [0, 1]
    assert {job_id for result in results for job_id in result} == {queued_job_id}
