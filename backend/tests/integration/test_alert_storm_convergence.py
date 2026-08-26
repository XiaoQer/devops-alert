from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.signal_intake import SignalCommand
from incident_intelligence.ids import new_id
from incident_intelligence.persistence.models import (
    AlertGroupCorrelationJobRow,
    AlertGroupMemberRow,
    AlertGroupRow,
    AlertRow,
    IncidentAlertLinkRow,
    IncidentRow,
    ServiceCatalogEntryRow,
    SignalEventRow,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_group_correlation import AlertGroupCorrelationService
from incident_intelligence.services.alert_group_correlation_jobs import (
    AlertGroupCorrelationJobService,
)
from incident_intelligence.services.alert_grouping import AlertGroupingService
from incident_intelligence.services.alert_grouping_jobs import AlertGroupingJobService
from incident_intelligence.services.signal_intake import SignalIntakeService

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)
SOURCE_ID = "src_00000000000000000000000000000002"


def _command(index: int) -> SignalCommand:
    return SignalCommand.model_validate(
        {
            "alert_source_id": SOURCE_ID,
            "source": "alertmanager",
            "source_instance": f"{index:064x}",
            "source_event_id": f"{index:064x}",
            "source_alert_key": f"payment-error-{index}",
            "event_type": "alert.firing",
            "event_at": NOW + timedelta(milliseconds=index),
            "episode_started_at": NOW,
            "title": f"支付实例 {index} 错误率升高",
            "summary": "支付接口错误率超过阈值",
            "severity": "high",
            "service": "payment-api",
            "environment": "production",
            "facts": {"symptom": "errors", "pod": f"payment-{index}"},
        }
    )


def _count(session: Session, model: type[object]) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_one_thousand_concurrent_alerts_converge_without_losing_audit_facts(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
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
    uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
    intake = SignalIntakeService(uow_factory=uow_factory, clock=lambda: NOW + timedelta(minutes=1))
    grouping_jobs = AlertGroupingJobService(uow_factory=uow_factory)
    grouping = AlertGroupingService(
        uow_factory=uow_factory, clock=lambda: NOW + timedelta(minutes=1)
    )
    correlation_jobs = AlertGroupCorrelationJobService(uow_factory=uow_factory)
    correlation = AlertGroupCorrelationService(
        uow_factory=uow_factory, clock=lambda: NOW + timedelta(minutes=1)
    )
    batches = [
        tuple(_command(index) for index in range(start, start + 100))
        for start in range(1, 1_001, 100)
    ]

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(
            executor.map(
                lambda item: intake.submit_batch(
                    item[1], "alertmanager-adapter", f"req-capacity-{item[0]}"
                ),
                enumerate(batches),
            )
        )
    assert sum(result.counts.opened for result in results) == 1_000

    replay = intake.submit_batch(batches[0], "alertmanager-adapter", "req-capacity-replay")
    assert all(item.replayed for item in replay.items)

    first = grouping_jobs.claim_batch(
        "capacity-first", NOW + timedelta(minutes=1), limit=1, lease_seconds=300
    )
    grouping.process(first[0])
    while leases := grouping_jobs.claim_batch(
        "capacity-workers", NOW + timedelta(minutes=1), limit=50, lease_seconds=300
    ):
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(grouping.process, leases))

    leases = correlation_jobs.claim_batch(
        "capacity-correlation", NOW + timedelta(minutes=1), limit=10, lease_seconds=300
    )
    assert len(leases) == 1
    result = correlation.process(leases[0])
    assert result.linked_alert_count == 1_000

    with session_factory() as session:
        group = session.scalar(select(AlertGroupRow))
        assert group is not None
        assert group.total_count == 1_000
        assert group.impacted_resource_count == 1_000
        assert group.storm_state == "STORM"
        assert _count(session, SignalEventRow) == 1_000
        assert _count(session, AlertRow) == 1_000
        assert _count(session, AlertGroupRow) == 1
        assert _count(session, AlertGroupMemberRow) == 1_000
        assert _count(session, IncidentRow) == 1
        assert _count(session, IncidentAlertLinkRow) == 1_000
        assert (
            session.scalar(
                select(func.count())
                .select_from(AlertGroupCorrelationJobRow)
                .where(AlertGroupCorrelationJobRow.active_slot == 1)
            )
            == 0
        )
