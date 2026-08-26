from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial
from threading import Barrier

from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from incident_intelligence.domain.signal_intake import SignalCommand
from incident_intelligence.ids import new_id
from incident_intelligence.persistence.models import (
    AlertGroupingJobRow,
    AlertGroupMemberRow,
    AlertGroupRow,
    AlertRow,
    CorrelationJobRow,
    IncidentAlertLinkRow,
    IncidentRow,
    ServiceCatalogEntryRow,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_group_backfill import AlertGroupBackfillService
from incident_intelligence.services.alert_group_correlation import AlertGroupCorrelationService
from incident_intelligence.services.alert_group_correlation_jobs import (
    AlertGroupCorrelationJobService,
)
from incident_intelligence.services.alert_grouping import AlertGroupingService
from incident_intelligence.services.alert_grouping_jobs import AlertGroupingJobService
from incident_intelligence.services.correlation import CorrelationService
from incident_intelligence.services.correlation_jobs import CorrelationJobService
from incident_intelligence.services.signal_intake import SignalIntakeService

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)
SOURCE_ID = "src_00000000000000000000000000000002"


def command(number: int) -> SignalCommand:
    return SignalCommand.model_validate(
        {
            "alert_source_id": SOURCE_ID,
            "source": "alertmanager",
            "source_instance": f"{number:064x}",
            "source_event_id": f"{number:064x}",
            "source_alert_key": f"historical-payment-{number}",
            "event_type": "alert.firing",
            "event_at": NOW + timedelta(seconds=number),
            "episode_started_at": NOW,
            "title": "支付接口异常",
            "summary": "支付接口错误率升高",
            "severity": "high",
            "service": "payment-api",
            "environment": "production",
            "facts": {"symptom": "errors", "pod": f"payment-{number}"},
        }
    )


def test_backfill_is_bounded_stable_and_idempotent(migrated_engine: Engine) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
    results = SignalIntakeService(uow_factory=uow_factory, clock=lambda: NOW).submit_batch(
        [command(number) for number in range(1, 4)],
        "alertmanager-adapter",
        "req-bounded",
    )
    alert_ids = sorted(item.alert_id for item in results.items if item.alert_id is not None)
    with session_factory.begin() as session:
        session.execute(delete(AlertGroupingJobRow))
    service = AlertGroupBackfillService(uow_factory=uow_factory)

    assert service.enqueue_batch(limit=2, now=NOW) == 2
    with session_factory() as session:
        queued_ids = list(
            session.scalars(
                select(AlertGroupingJobRow.alert_id).order_by(AlertGroupingJobRow.alert_id)
            )
        )
    assert queued_ids == alert_ids[:2]
    assert service.enqueue_batch(limit=2, now=NOW) == 1
    assert service.enqueue_batch(limit=2, now=NOW) == 0


def test_concurrent_backfill_workers_partition_locked_alerts(migrated_engine: Engine) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
    SignalIntakeService(uow_factory=uow_factory, clock=lambda: NOW).submit_batch(
        [command(number) for number in range(1, 4)],
        "alertmanager-adapter",
        "req-concurrent",
    )
    with session_factory.begin() as session:
        session.execute(delete(AlertGroupingJobRow))
    service = AlertGroupBackfillService(uow_factory=uow_factory)
    ready = Barrier(2)

    def enqueue() -> int:
        ready.wait(timeout=5)
        return service.enqueue_batch(limit=2, now=NOW)

    with ThreadPoolExecutor(max_workers=2) as executor:
        counts = list(executor.map(lambda _: enqueue(), range(2)))

    assert sum(counts) == 3
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AlertGroupingJobRow)) == 3


def test_active_legacy_job_drains_before_backfill_and_existing_incident_is_inherited(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
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
    results = SignalIntakeService(uow_factory=uow_factory, clock=lambda: NOW).submit_batch(
        [command(number) for number in range(1, 4)],
        "alertmanager-adapter",
        "req-history",
    )
    alert_ids = [item.alert_id for item in results.items]
    assert all(alert_id is not None for alert_id in alert_ids)
    linked_id, pending_id, completed_id = (str(alert_id) for alert_id in alert_ids)
    incident_id = new_id("inc")
    with session_factory.begin() as session:
        session.execute(delete(AlertGroupingJobRow))
        linked_alert = session.get(AlertRow, linked_id)
        assert linked_alert is not None
        session.add(
            IncidentRow(
                id=incident_id,
                primary_alert_id=linked_id,
                state="DETECTED",
                title=linked_alert.title,
                severity=linked_alert.severity,
                service=linked_alert.service,
                environment=linked_alert.environment,
                detected_at=linked_alert.last_observed_at,
                state_changed_at=NOW,
                resolved_at=None,
                closed_at=None,
                created_at=NOW,
                version=1,
            )
        )
        session.flush()
        session.add(
            IncidentAlertLinkRow(
                incident_id=incident_id,
                alert_id=linked_id,
                relation="PRIMARY",
                decision_id=None,
                group_decision_id=None,
                linked_at=NOW,
                created_at=NOW,
            )
        )
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert uow.correlation is not None
        for alert_id in (linked_id, pending_id, completed_id):
            alert = uow.correlation.find_alert_for_update(alert_id)
            assert alert is not None
            uow.correlation.enqueue(
                alert_source_id=alert.alert_source_id,
                alert_id=alert.id,
                alert_version=alert.version,
                now=NOW,
            )
        uow.commit()
    with session_factory.begin() as session:
        session.execute(
            CorrelationJobRow.__table__.update()
            .where(CorrelationJobRow.alert_id.in_((linked_id, completed_id)))
            .values(state="SUCCEEDED")
        )

    backfill = AlertGroupBackfillService(uow_factory=uow_factory)
    assert backfill.enqueue_batch(limit=100, now=NOW) == 2
    with session_factory() as session:
        queued = set(session.scalars(select(AlertGroupingJobRow.alert_id)))
    assert queued == {linked_id, completed_id}

    grouping_jobs = AlertGroupingJobService(uow_factory=uow_factory)
    grouping = AlertGroupingService(uow_factory=uow_factory, clock=lambda: NOW)
    for lease in grouping_jobs.claim_batch("grouping", NOW, limit=10, lease_seconds=30):
        grouping.process(lease)
    with session_factory() as session:
        group = session.scalar(select(AlertGroupRow))
    assert group is not None
    assert group.incident_id == incident_id

    old_jobs = CorrelationJobService(uow_factory=uow_factory)
    old_correlation = CorrelationService(uow_factory=uow_factory, clock=lambda: NOW)
    old_correlation.process(old_jobs.claim_batch("legacy", NOW, limit=1, lease_seconds=30)[0])
    assert backfill.enqueue_batch(limit=100, now=NOW) == 1
    grouping.process(grouping_jobs.claim_batch("grouping", NOW, limit=1, lease_seconds=30)[0])

    group_jobs = AlertGroupCorrelationJobService(uow_factory=uow_factory)
    group_correlation = AlertGroupCorrelationService(uow_factory=uow_factory, clock=lambda: NOW)
    while leases := group_jobs.claim_batch("group-correlation", NOW, limit=10, lease_seconds=30):
        for lease in leases:
            group_correlation.process(lease)

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AlertGroupMemberRow)) == 3
        assert session.scalar(select(func.count()).select_from(IncidentRow)) == 1
        assert session.scalar(select(func.count()).select_from(IncidentAlertLinkRow)) == 3
        assert (
            session.scalar(
                select(func.count())
                .select_from(CorrelationJobRow)
                .where(CorrelationJobRow.state.in_(("PENDING", "LEASED")))
            )
            == 0
        )
