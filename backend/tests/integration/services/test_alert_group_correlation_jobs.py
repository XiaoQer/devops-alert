from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.persistence.models import (
    AlertGroupCorrelationJobRow,
    AlertGroupRow,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_group_correlation_jobs import (
    AlertGroupCorrelationJobNotRetryable,
    AlertGroupCorrelationJobService,
)
from tests.integration.persistence.test_constraints import make_alert_group, seed_alert

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)


@pytest.fixture
def session_factory(migrated_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def group_id(session_factory: sessionmaker[Session]) -> str:
    with session_factory.begin() as session:
        alert = seed_alert(session)
        group = make_alert_group(alert.id, version=101)
        session.add(group)
    return group.id


@pytest.fixture
def jobs(session_factory: sessionmaker[Session]) -> AlertGroupCorrelationJobService:
    return AlertGroupCorrelationJobService(
        uow_factory=partial(SqlAlchemyUnitOfWork, session_factory)
    )


def test_pending_schedules_converge_to_one_latest_target(
    jobs: AlertGroupCorrelationJobService,
    session_factory: sessionmaker[Session],
    group_id: str,
) -> None:
    for version in range(1, 102):
        jobs.schedule(group_id, version, NOW)

    with session_factory() as session:
        active = list(
            session.scalars(
                select(AlertGroupCorrelationJobRow).where(
                    AlertGroupCorrelationJobRow.active_slot == 1
                )
            )
        )
        group = session.get(AlertGroupRow, group_id)

    assert len(active) == 1
    assert active[0].target_group_version == 101
    assert group is not None
    assert group.desired_correlation_version == 101


def test_expired_lease_can_be_reclaimed(
    jobs: AlertGroupCorrelationJobService,
    group_id: str,
) -> None:
    jobs.schedule(group_id, 101, NOW)
    first = jobs.claim_batch("runner-a", NOW, limit=1, lease_seconds=30)[0]

    assert jobs.claim_batch("runner-b", NOW, limit=1, lease_seconds=30) == ()
    reclaimed = jobs.claim_batch(
        "runner-b", NOW + timedelta(seconds=31), limit=1, lease_seconds=30
    )[0]

    assert reclaimed.id == first.id
    assert reclaimed.attempts == 2
    assert reclaimed.lease_owner == "runner-b"


def test_fifth_failure_is_terminal_and_can_be_manually_retried(
    jobs: AlertGroupCorrelationJobService,
    group_id: str,
) -> None:
    jobs.schedule(group_id, 101, NOW)
    current = NOW
    job_id = ""
    for attempt in range(1, 6):
        lease = jobs.claim_batch("runner", current, limit=1, lease_seconds=30)[0]
        job_id = lease.id
        state = jobs.fail(lease.id, "runner", "processing_failed", current)
        assert state == ("FAILED" if attempt == 5 else "PENDING")
        current += timedelta(minutes=10)

    with pytest.raises(AlertGroupCorrelationJobNotRetryable):
        jobs.fail(job_id, "runner", "processing_failed", current)

    retried = jobs.retry_failed(job_id, current)
    assert retried.state == "PENDING"
    assert retried.attempts == 0
    assert retried.last_error_code is None

    with pytest.raises(AlertGroupCorrelationJobNotRetryable):
        jobs.retry_failed(job_id, current)


def test_concurrent_schedules_keep_one_active_job(
    jobs: AlertGroupCorrelationJobService,
    session_factory: sessionmaker[Session],
    group_id: str,
) -> None:
    ready = Barrier(2)

    def schedule(version: int) -> str:
        ready.wait(timeout=5)
        return jobs.schedule(group_id, version, NOW)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(schedule, [1, 101]))

    assert results == ["SCHEDULED", "SCHEDULED"]
    with session_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AlertGroupCorrelationJobRow)
                .where(AlertGroupCorrelationJobRow.active_slot == 1)
            )
            == 1
        )
        job = session.scalar(
            select(AlertGroupCorrelationJobRow).where(AlertGroupCorrelationJobRow.active_slot == 1)
        )
    assert job is not None
    assert job.target_group_version == 101
