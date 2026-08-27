from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import partial

from sqlalchemy import func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.signal_intake import SignalCommand
from incident_intelligence.ids import new_id
from incident_intelligence.persistence.models import (
    AlertEventLifecycleJobRow,
    AlertGroupMemberRow,
    AlertGroupRow,
    ServiceCatalogEntryRow,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_event_lifecycle import AlertEventLifecycleService
from incident_intelligence.services.alert_event_lifecycle_jobs import AlertEventLifecycleJobService
from incident_intelligence.services.alert_grouping import AlertGroupingService
from incident_intelligence.services.alert_grouping_jobs import AlertGroupingJobService
from incident_intelligence.services.signal_intake import SignalIntakeService

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
SOURCE_ID = "src_00000000000000000000000000000002"


def command(number: int, **overrides: object) -> SignalCommand:
    values: dict[str, object] = {
        "alert_source_id": SOURCE_ID,
        "source": "alertmanager",
        "source_instance": f"{number:064x}",
        "source_event_id": f"{number:064x}",
        "source_alert_key": f"payment-error-{number}",
        "event_type": "alert.firing",
        "event_at": NOW + timedelta(seconds=number),
        "episode_started_at": NOW + timedelta(seconds=number),
        "title": "支付接口错误率升高",
        "summary": "支付接口错误率超过阈值",
        "severity": "high",
        "service": "payment-api",
        "environment": "production",
        "facts": {"alertname": "PaymentErrorsHigh", "symptom": "errors"},
    }
    values.update(overrides)
    return SignalCommand.model_validate(values)


def build_services(
    session_factory: sessionmaker[Session], clock: list[datetime]
) -> tuple[
    SignalIntakeService,
    AlertGroupingJobService,
    AlertGroupingService,
    AlertEventLifecycleJobService,
    AlertEventLifecycleService,
]:
    uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
    return (
        SignalIntakeService(uow_factory=uow_factory, clock=lambda: clock[0]),
        AlertGroupingJobService(uow_factory=uow_factory),
        AlertGroupingService(uow_factory=uow_factory, clock=lambda: clock[0]),
        AlertEventLifecycleJobService(uow_factory=uow_factory),
        AlertEventLifecycleService(uow_factory=uow_factory, clock=lambda: clock[0]),
    )


def seed_catalog(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        session.add(
            ServiceCatalogEntryRow(
                id=new_id("svc"),
                service="payment-api",
                environment="production",
                owner_team="payments",
                state="ACTIVE",
                created_at=NOW,
                updated_at=NOW,
                version=1,
            )
        )


def process_one_grouping(
    jobs: AlertGroupingJobService,
    grouping: AlertGroupingService,
    now: datetime,
) -> str:
    lease = jobs.claim_batch("grouping-runner", now, limit=1, lease_seconds=30)[0]
    result = grouping.process(lease)
    assert result.group_id is not None
    return result.group_id


def process_one_lifecycle(
    jobs: AlertEventLifecycleJobService,
    lifecycle: AlertEventLifecycleService,
    now: datetime,
) -> None:
    lease = jobs.claim_batch("lifecycle-runner", now, limit=1, lease_seconds=30)[0]
    lifecycle.process(lease)


def test_forming_resolved_observing_and_closed_are_persisted(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    seed_catalog(session_factory)
    clock = [NOW]
    intake, grouping_jobs, grouping, lifecycle_jobs, lifecycle = build_services(
        session_factory, clock
    )

    initial = command(1)
    intake.submit_batch([initial], "alertmanager-adapter", "req-open")
    group_id = process_one_grouping(grouping_jobs, grouping, clock[0])
    with session_factory() as session:
        group = session.get(AlertGroupRow, group_id)
        assert group is not None
        assert group.state == "FORMING"
        assert group.forming_until == NOW + timedelta(seconds=30)
        assert session.scalar(select(func.count()).select_from(AlertEventLifecycleJobRow)) == 1

    clock[0] = NOW + timedelta(seconds=31)
    process_one_lifecycle(lifecycle_jobs, lifecycle, clock[0])
    with session_factory() as session:
        group = session.get(AlertGroupRow, group_id)
        assert group is not None
        assert group.state == "ACTIVE"

    clock[0] = NOW + timedelta(minutes=1)
    intake.submit_batch(
        [
            initial.model_copy(
                update={
                    "source_event_id": "f" * 64,
                    "event_type": "alert.resolved",
                    "event_at": clock[0],
                }
            )
        ],
        "alertmanager-adapter",
        "req-resolve",
    )
    assert process_one_grouping(grouping_jobs, grouping, clock[0]) == group_id
    with session_factory() as session:
        group = session.get(AlertGroupRow, group_id)
        assert group is not None
        assert group.state == "OBSERVING"
        assert group.observing_until == clock[0] + timedelta(minutes=5)

    clock[0] = NOW + timedelta(minutes=6)
    process_one_lifecycle(lifecycle_jobs, lifecycle, clock[0])
    with session_factory() as session:
        group = session.get(AlertGroupRow, group_id)
        assert group is not None
        assert group.state == "CLOSED"
        assert group.closed_at == clock[0]


def test_new_trigger_after_closed_creates_recurrence_instead_of_reopening(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    seed_catalog(session_factory)
    clock = [NOW]
    intake, grouping_jobs, grouping, lifecycle_jobs, lifecycle = build_services(
        session_factory, clock
    )
    first = command(1)
    intake.submit_batch([first], "alertmanager-adapter", "req-open")
    original_group_id = process_one_grouping(grouping_jobs, grouping, clock[0])
    clock[0] = NOW + timedelta(seconds=31)
    process_one_lifecycle(lifecycle_jobs, lifecycle, clock[0])
    clock[0] = NOW + timedelta(minutes=1)
    intake.submit_batch(
        [
            first.model_copy(
                update={
                    "source_event_id": "e" * 64,
                    "event_type": "alert.resolved",
                    "event_at": clock[0],
                }
            )
        ],
        "alertmanager-adapter",
        "req-resolve",
    )
    process_one_grouping(grouping_jobs, grouping, clock[0])
    clock[0] = NOW + timedelta(minutes=6)
    process_one_lifecycle(lifecycle_jobs, lifecycle, clock[0])

    clock[0] = NOW + timedelta(minutes=7)
    intake.submit_batch(
        [
            first.model_copy(
                update={
                    "source_event_id": "d" * 64,
                    "event_type": "alert.firing",
                    "event_at": clock[0],
                    "episode_started_at": clock[0],
                }
            )
        ],
        "alertmanager-adapter",
        "req-reopen",
    )
    recurrence_group_id = process_one_grouping(grouping_jobs, grouping, clock[0])

    assert recurrence_group_id != original_group_id
    with session_factory() as session:
        groups = tuple(session.scalars(select(AlertGroupRow).order_by(AlertGroupRow.created_at)))
        assert len(groups) == 2
        assert groups[0].state == "CLOSED"
        assert groups[0].problem_key == groups[1].problem_key


def test_member_over_limit_rolls_to_continuation_event(migrated_engine: Engine) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    seed_catalog(session_factory)
    clock = [NOW]
    intake, grouping_jobs, grouping, _, _ = build_services(session_factory, clock)
    intake.submit_batch([command(1)], "alertmanager-adapter", "req-first")
    original_group_id = process_one_grouping(grouping_jobs, grouping, clock[0])
    with session_factory.begin() as session:
        session.execute(
            update(AlertGroupRow)
            .where(AlertGroupRow.id == original_group_id)
            .values(member_limit=1)
        )

    clock[0] = NOW + timedelta(seconds=1)
    intake.submit_batch([command(2)], "alertmanager-adapter", "req-second")
    continuation_id = process_one_grouping(grouping_jobs, grouping, clock[0])

    assert continuation_id != original_group_id
    with session_factory() as session:
        continuation = session.get(AlertGroupRow, continuation_id)
        assert continuation is not None
        assert continuation.continuation_group_id == original_group_id
        assert session.scalar(select(func.count()).select_from(AlertGroupMemberRow)) == 2


def test_late_active_alert_within_tolerance_corrects_recently_closed_event(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    seed_catalog(session_factory)
    clock = [NOW]
    intake, grouping_jobs, grouping, lifecycle_jobs, lifecycle = build_services(
        session_factory, clock
    )
    first = command(1)
    intake.submit_batch([first], "alertmanager-adapter", "req-open")
    group_id = process_one_grouping(grouping_jobs, grouping, clock[0])
    clock[0] = NOW + timedelta(seconds=31)
    process_one_lifecycle(lifecycle_jobs, lifecycle, clock[0])
    clock[0] = NOW + timedelta(minutes=1)
    intake.submit_batch(
        [
            first.model_copy(
                update={
                    "source_event_id": "c" * 64,
                    "event_type": "alert.resolved",
                    "event_at": clock[0],
                }
            )
        ],
        "alertmanager-adapter",
        "req-resolve",
    )
    process_one_grouping(grouping_jobs, grouping, clock[0])
    clock[0] = NOW + timedelta(minutes=6)
    process_one_lifecycle(lifecycle_jobs, lifecycle, clock[0])

    clock[0] = NOW + timedelta(minutes=8)
    late = command(
        2,
        event_at=NOW + timedelta(minutes=5),
        episode_started_at=NOW + timedelta(minutes=5),
    )
    intake.submit_batch([late], "alertmanager-adapter", "req-late")
    late_group_id = process_one_grouping(grouping_jobs, grouping, clock[0])

    assert late_group_id == group_id
    with session_factory() as session:
        group = session.get(AlertGroupRow, group_id)
        assert group is not None
        assert group.state == "ACTIVE"
        assert group.closed_at is None
        assert group.total_count == 2


def test_expired_lease_is_reclaimed_after_process_restart(migrated_engine: Engine) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    seed_catalog(session_factory)
    clock = [NOW]
    intake, grouping_jobs, grouping, lifecycle_jobs, _ = build_services(session_factory, clock)
    intake.submit_batch([command(1)], "alertmanager-adapter", "req-open")
    process_one_grouping(grouping_jobs, grouping, clock[0])

    due = NOW + timedelta(seconds=30)
    first = lifecycle_jobs.claim_batch("old-process", due, limit=1, lease_seconds=30)[0]
    reclaimed = lifecycle_jobs.claim_batch(
        "new-process",
        due + timedelta(seconds=31),
        limit=1,
        lease_seconds=30,
    )[0]

    assert reclaimed.id == first.id
    assert reclaimed.attempts == 2
    assert reclaimed.lease_owner == "new-process"
