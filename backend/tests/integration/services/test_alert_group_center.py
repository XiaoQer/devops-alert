from __future__ import annotations

from datetime import UTC, datetime
from functools import partial

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from incident_intelligence.ids import new_id
from incident_intelligence.persistence.models import AlertGroupRow, ServiceCatalogEntryRow
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_group_center import (
    AlertGroupCenterService,
    AlertGroupFilters,
)
from incident_intelligence.services.alert_group_correlation import AlertGroupCorrelationService
from incident_intelligence.services.alert_group_correlation_jobs import (
    AlertGroupCorrelationJobService,
)
from incident_intelligence.services.alert_grouping import AlertGroupingService
from incident_intelligence.services.alert_grouping_jobs import AlertGroupingJobService
from incident_intelligence.services.signal_intake import SignalIntakeService
from tests.integration.services.test_alert_group_correlation_service import command

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)


def test_group_center_exposes_real_counts_second_member_page_and_incident_groups(
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
    SignalIntakeService(uow_factory=uow_factory, clock=lambda: NOW).submit_batch(
        [command(index) for index in range(1, 102)],
        "alertmanager-adapter",
        "req-group-center",
    )
    grouping_jobs = AlertGroupingJobService(uow_factory=uow_factory)
    grouping = AlertGroupingService(uow_factory=uow_factory, clock=lambda: NOW)
    while leases := grouping_jobs.claim_batch("grouping", NOW, limit=50, lease_seconds=30):
        for lease in leases:
            grouping.process(lease)
    with session_factory() as session:
        group_id = session.scalar(select(AlertGroupRow.id))
    assert group_id is not None

    center = AlertGroupCenterService(session_factory=session_factory)
    page = center.list_groups(
        AlertGroupFilters(
            state="ACTIVE",
            severity="high",
            service="payment-api",
            environment="production",
            storm_state="STORM",
            incident_linked=False,
        )
    )
    assert page.total == 1
    assert page.items[0].total_count == 101
    assert page.items[0].impacted_resource_count == 101
    assert page.items[0].problem_type == "支付接口错误率升高"
    assert page.items[0].scope_type == "SERVICE"
    assert page.items[0].scope_display_name == "payment-api"
    assert page.items[0].signature_version == "problem-signature.v1"
    assert page.items[0].incident is None

    summary = center.summarize("24h", now=NOW)
    assert summary.active_groups == 1
    assert summary.severe_active_groups == 1
    assert summary.active_alerts == 101
    assert summary.storm_groups == 1
    assert summary.compression_ratio == 101.0
    assert summary.peak_rate_per_minute == 101
    assert summary.pending_group_jobs == 1

    overview = center.get_overview(group_id)
    assert overview.group.total_count == 101
    assert overview.source_distribution[0].count == 101
    assert overview.severity_distribution[0].name == "high"
    assert len(overview.impacted_resources) == 20
    assert center.list_members(group_id, limit=100, offset=0).total == 101
    second_page = center.list_members(group_id, limit=100, offset=100)
    assert second_page.total == 101
    assert len(second_page.items) == 1

    correlation_jobs = AlertGroupCorrelationJobService(uow_factory=uow_factory)
    correlation = AlertGroupCorrelationService(uow_factory=uow_factory, clock=lambda: NOW)
    correlation.process(
        correlation_jobs.claim_batch("correlation", NOW, limit=1, lease_seconds=30)[0]
    )
    overview = center.get_overview(group_id)
    assert overview.group.incident is not None
    incident_page = center.list_incident_groups(overview.group.incident.id, limit=20, offset=0)
    assert incident_page.total == 1
    assert incident_page.items[0].id == group_id
