from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import partial

from sqlalchemy import func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from incident_intelligence.domain.signal_intake import SignalCommand
from incident_intelligence.ids import new_id
from incident_intelligence.persistence.models import (
    AlertGroupCorrelationJobRow,
    AlertGroupDecisionRow,
    AlertGroupRow,
    AlertRow,
    IncidentAlertLinkRow,
    IncidentRow,
    ServiceCatalogEntryRow,
    SignalEventRow,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_group_correlation import (
    AlertGroupCorrelationService,
)
from incident_intelligence.services.alert_group_correlation_jobs import (
    AlertGroupCorrelationJobService,
)
from incident_intelligence.services.alert_grouping import AlertGroupingService
from incident_intelligence.services.alert_grouping_jobs import AlertGroupingJobService
from incident_intelligence.services.signal_intake import SignalIntakeService

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)
SOURCE_ID = "src_00000000000000000000000000000002"


def command(number: int, *, symptom: str = "errors") -> SignalCommand:
    return SignalCommand.model_validate(
        {
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
            "facts": {"symptom": symptom, "pod": f"payment-{number}"},
        }
    )


def no_service_command(number: int) -> SignalCommand:
    return SignalCommand.model_validate(
        {
            "alert_source_id": SOURCE_ID,
            "source": "alertmanager",
            "source_instance": f"{number:064x}",
            "source_event_id": f"{number:064x}",
            "source_alert_key": f"pod-not-ready-{number}",
            "event_type": "alert.firing",
            "event_at": NOW + timedelta(seconds=number),
            "episode_started_at": NOW,
            "title": "Pod 未就绪",
            "summary": "Pod 持续未就绪",
            "severity": "high",
            "service": None,
            "environment": "production",
            "facts": {
                "alertname": "KubePodNotReady",
                "namespace": "payments",
                "pod": f"payment-{number}",
                "symptom": "unknown",
            },
        }
    )


def test_group_without_service_records_skip_and_does_not_create_incident(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
    intake = SignalIntakeService(uow_factory=uow_factory, clock=lambda: NOW)
    grouping_jobs = AlertGroupingJobService(uow_factory=uow_factory)
    grouping = AlertGroupingService(uow_factory=uow_factory, clock=lambda: NOW)
    correlation_jobs = AlertGroupCorrelationJobService(uow_factory=uow_factory)
    correlation = AlertGroupCorrelationService(uow_factory=uow_factory, clock=lambda: NOW)

    intake.submit_batch(
        [no_service_command(1), no_service_command(2)],
        "alertmanager-adapter",
        "req-no-service",
    )
    for lease in grouping_jobs.claim_batch("grouping", NOW, limit=10, lease_seconds=30):
        grouping.process(lease)

    leases = correlation_jobs.claim_batch("correlation", NOW, limit=10, lease_seconds=30)
    assert len(leases) == 1
    result = correlation.process(leases[0])

    assert result.outcome == "SKIPPED_SERVICE_MISSING"
    assert result.incident_id is None
    assert result.linked_alert_count == 0
    with session_factory() as session:
        group = session.scalar(select(AlertGroupRow))
        decision = session.scalar(select(AlertGroupDecisionRow))
        assert group is not None
        assert group.total_count == 2
        assert group.incident_id is None
        assert decision is not None
        assert decision.reason_codes == ["service_missing"]
        assert decision.facts["problem_type"] == "KubePodNotReady"
        assert decision.facts["signature_version"] == "problem-signature.v1"
        assert session.scalar(select(func.count()).select_from(IncidentRow)) == 0
        for model in (SignalEventRow, AlertRow, AlertGroupRow):
            session.execute(
                update(model).where(model.service.is_(None)).values(service="test-cleanup")
            )
        session.commit()


def test_group_correlation_links_every_member_and_catches_up_after_leased_update(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    with session_factory() as session:
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
        session.commit()
    uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
    intake = SignalIntakeService(uow_factory=uow_factory, clock=lambda: NOW)
    grouping_jobs = AlertGroupingJobService(uow_factory=uow_factory)
    grouping = AlertGroupingService(uow_factory=uow_factory, clock=lambda: NOW)
    correlation_jobs = AlertGroupCorrelationJobService(uow_factory=uow_factory)
    correlation = AlertGroupCorrelationService(uow_factory=uow_factory, clock=lambda: NOW)

    intake.submit_batch([command(1)], "alertmanager-adapter", "req-1")
    grouping.process(grouping_jobs.claim_batch("grouping", NOW, limit=1, lease_seconds=30)[0])
    first_lease = correlation_jobs.claim_batch("correlation", NOW, limit=1, lease_seconds=30)[0]

    intake.submit_batch([command(2)], "alertmanager-adapter", "req-2")
    grouping.process(grouping_jobs.claim_batch("grouping", NOW, limit=1, lease_seconds=30)[0])
    superseded = correlation.process(first_lease)
    assert superseded.outcome == "SUPERSEDED"
    assert superseded.incident_id is None

    successor = correlation_jobs.claim_batch("correlation", NOW, limit=1, lease_seconds=30)[0]
    completed = correlation.process(successor)

    assert completed.outcome == "CREATED_NO_MATCH"
    assert completed.linked_alert_count == 2
    with session_factory() as session:
        group = session.scalar(select(AlertGroupRow))
        assert group is not None
        assert group.incident_id == completed.incident_id
        assert session.scalar(select(func.count()).select_from(IncidentRow)) == 1
        assert session.scalar(select(func.count()).select_from(IncidentAlertLinkRow)) == 2
        assert session.scalar(select(func.count()).select_from(AlertGroupDecisionRow)) == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(AlertGroupCorrelationJobRow)
                .where(AlertGroupCorrelationJobRow.active_slot == 1)
            )
            == 0
        )


def test_pending_group_correlation_schedule_collapses_to_latest_version(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
    with session_factory() as session:
        from tests.integration.persistence.test_constraints import (
            make_alert_group,
            seed_alert,
        )

        alert = seed_alert(session)
        group = make_alert_group(alert.id, version=101)
        session.add(group)
        session.commit()
        group_id = group.id
    jobs = AlertGroupCorrelationJobService(uow_factory=uow_factory)

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


def test_one_hundred_one_members_create_one_incident_and_one_active_task(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    with session_factory() as session:
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
        session.commit()
    uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
    intake = SignalIntakeService(uow_factory=uow_factory, clock=lambda: NOW)
    grouping_jobs = AlertGroupingJobService(uow_factory=uow_factory)
    grouping = AlertGroupingService(uow_factory=uow_factory, clock=lambda: NOW)
    correlation_jobs = AlertGroupCorrelationJobService(uow_factory=uow_factory)
    correlation = AlertGroupCorrelationService(uow_factory=uow_factory, clock=lambda: NOW)
    intake.submit_batch(
        [command(index) for index in range(1, 102)],
        "alertmanager-adapter",
        "req-storm",
    )

    while leases := grouping_jobs.claim_batch("grouping", NOW, limit=50, lease_seconds=30):
        for lease in leases:
            grouping.process(lease)

    group_lease = correlation_jobs.claim_batch("correlation", NOW, limit=10, lease_seconds=30)[0]
    result = correlation.process(group_lease)

    assert result.linked_alert_count == 101
    with session_factory() as session:
        group = session.scalar(select(AlertGroupRow))
        assert group is not None
        assert group.total_count == 101
        assert group.storm_state == "STORM"
        assert session.scalar(select(func.count()).select_from(IncidentRow)) == 1
        assert session.scalar(select(func.count()).select_from(IncidentAlertLinkRow)) == 101
        assert session.scalar(select(func.count()).select_from(AlertGroupDecisionRow)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(AlertGroupCorrelationJobRow)
                .where(AlertGroupCorrelationJobRow.active_slot == 1)
            )
            == 0
        )


def test_three_symptoms_for_same_service_converge_before_incident_correlation(
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
    intake = SignalIntakeService(uow_factory=uow_factory, clock=lambda: NOW)
    grouping_jobs = AlertGroupingJobService(uow_factory=uow_factory)
    grouping = AlertGroupingService(uow_factory=uow_factory, clock=lambda: NOW)
    correlation_jobs = AlertGroupCorrelationJobService(uow_factory=uow_factory)
    correlation = AlertGroupCorrelationService(uow_factory=uow_factory, clock=lambda: NOW)

    intake.submit_batch(
        [
            command(1, symptom="errors"),
            command(2, symptom="latency"),
            command(3, symptom="pod-restarts"),
        ],
        "alertmanager-adapter",
        "req-three-symptoms",
    )
    for lease in grouping_jobs.claim_batch("grouping", NOW, limit=10, lease_seconds=30):
        grouping.process(lease)
    for lease in correlation_jobs.claim_batch("correlation", NOW, limit=10, lease_seconds=30):
        correlation.process(lease)

    with session_factory() as session:
        outcomes = set(session.scalars(select(AlertGroupDecisionRow.outcome)))
        group = session.scalar(select(AlertGroupRow))
        assert group is not None
        assert group.total_count == 3
        assert session.scalar(select(func.count()).select_from(IncidentRow)) == 1
        assert session.scalar(select(func.count()).select_from(IncidentAlertLinkRow)) == 3
    assert outcomes == {"CREATED_NO_MATCH"}
