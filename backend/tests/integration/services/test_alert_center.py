from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.signal_intake import SignalCommand
from incident_intelligence.persistence.models import SignalEventRow, SignalIntakeResultRow
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_center import (
    AlertCenterService,
    AlertListFilters,
    AlertResourceNotFound,
)
from incident_intelligence.services.alert_group_correlation import AlertGroupCorrelationService
from incident_intelligence.services.alert_group_correlation_jobs import (
    AlertGroupCorrelationJobService,
)
from incident_intelligence.services.alert_grouping import AlertGroupingService
from incident_intelligence.services.alert_grouping_jobs import AlertGroupingJobService
from incident_intelligence.services.alert_sources import (
    AlertSourceService,
    CreateAlertSourceCommand,
)
from incident_intelligence.services.catalog import CreateServiceCommand, ServiceCatalogService
from incident_intelligence.services.signal_intake import SignalIntakeService

NOW = datetime(2026, 8, 26, 13, 0, tzinfo=UTC)


@dataclass(frozen=True)
class SeededAlerts:
    service: AlertCenterService
    linked_alert_id: str
    resolved_alert_id: str
    source_a_id: str
    incident_id: str


def _command(
    source_id: str,
    *,
    event_id: str,
    alert_key: str,
    service: str,
    severity: str,
    event_type: str = "alert.firing",
    event_at: datetime = NOW,
    summary: str = "检测结果",
) -> SignalCommand:
    return SignalCommand.model_validate(
        {
            "alert_source_id": source_id,
            "source": "alertmanager",
            "source_instance": "1" * 64,
            "source_event_id": event_id,
            "source_alert_key": alert_key,
            "event_type": event_type,
            "event_at": event_at,
            "episode_started_at": NOW,
            "title": f"{service} 告警",
            "summary": summary,
            "severity": severity,
            "service": service,
            "environment": "production",
            "facts": {"metric": "http_error_rate", "value": "18.4%"},
        }
    )


@pytest.fixture
def seeded(migrated_engine: Engine) -> SeededAlerts:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
    source_service = AlertSourceService(uow_factory=uow_factory, clock=lambda: NOW)
    source_a = source_service.create_source(
        CreateAlertSourceCommand(name="生产 Alertmanager", source_type="ALERTMANAGER"),
        idempotency_key="create-source-a",
        actor="manual-api-client",
        request_id="req-source-a",
    ).source
    source_b = source_service.create_source(
        CreateAlertSourceCommand(name="订单 Alertmanager", source_type="ALERTMANAGER"),
        idempotency_key="create-source-b",
        actor="manual-api-client",
        request_id="req-source-b",
    ).source
    ServiceCatalogService(uow_factory=uow_factory, clock=lambda: NOW).create_service(
        CreateServiceCommand(
            service="payment-api",
            environment="production",
            owner_team="payments",
        ),
        actor="manual-api-client",
        request_id="req-catalog",
    )
    intake = SignalIntakeService(uow_factory=uow_factory, clock=lambda: NOW)
    opened = intake.submit_batch(
        (
            _command(
                source_a.id,
                event_id="1" * 64,
                alert_key="payment-errors",
                service="payment-api",
                severity="high",
                summary="错误率达到 17.9%",
            ),
            _command(
                source_b.id,
                event_id="2" * 64,
                alert_key="order-latency",
                service="order-api",
                severity="medium",
            ),
        ),
        actor="alert-source",
        request_id="req-open",
    )
    linked_alert_id = opened.items[0].alert_id
    resolved_alert_id = opened.items[1].alert_id
    assert linked_alert_id is not None and resolved_alert_id is not None
    intake.submit_batch(
        (
            _command(
                source_a.id,
                event_id="3" * 64,
                alert_key="payment-errors",
                service="payment-api",
                severity="critical",
                event_at=NOW + timedelta(minutes=1),
                summary="错误率达到 18.4%",
            ),
            _command(
                source_b.id,
                event_id="4" * 64,
                alert_key="order-latency",
                service="order-api",
                severity="medium",
                event_type="alert.resolved",
                event_at=NOW + timedelta(minutes=2),
            ),
        ),
        actor="alert-source",
        request_id="req-update",
    )

    grouping_jobs = AlertGroupingJobService(uow_factory=uow_factory)
    grouping = AlertGroupingService(
        uow_factory=uow_factory, clock=lambda: NOW + timedelta(minutes=3)
    )
    while leases := grouping_jobs.claim_batch(
        "alert-center-test",
        NOW + timedelta(minutes=3),
        limit=10,
        lease_seconds=30,
    ):
        for lease in leases:
            grouping.process(lease)

    correlation_jobs = AlertGroupCorrelationJobService(uow_factory=uow_factory)
    correlation = AlertGroupCorrelationService(
        uow_factory=uow_factory, clock=lambda: NOW + timedelta(minutes=3)
    )
    while leases := correlation_jobs.claim_batch(
        "alert-center-test",
        NOW + timedelta(minutes=3),
        limit=10,
        lease_seconds=30,
    ):
        for lease in leases:
            correlation.process(lease)

    overview_service = AlertCenterService(session_factory=session_factory)
    overview = overview_service.get_overview(linked_alert_id)
    assert overview.correlation.incident is not None
    return SeededAlerts(
        service=overview_service,
        linked_alert_id=linked_alert_id,
        resolved_alert_id=resolved_alert_id,
        source_a_id=source_a.id,
        incident_id=overview.correlation.incident.id,
    )


def test_list_filters_by_source_service_time_and_incident_link(seeded: SeededAlerts) -> None:
    page = seeded.service.list_alerts(
        AlertListFilters(
            alert_source_id=seeded.source_a_id,
            state="ACTIVE",
            severity="critical",
            service="payment-api",
            environment="production",
            incident_linked=True,
            observed_from=NOW,
            observed_to=NOW + timedelta(minutes=5),
            limit=100,
            offset=0,
        )
    )
    assert [item.id for item in page.items] == [seeded.linked_alert_id]
    assert page.total == 1
    assert page.items[0].source.name == "生产 Alertmanager"
    assert page.items[0].incident_id == seeded.incident_id


def test_summary_and_overview_expose_bounded_normalized_facts(seeded: SeededAlerts) -> None:
    summary = seeded.service.summarize("24h", now=NOW + timedelta(minutes=5))
    assert summary.active == 1
    assert summary.severe_active == 1
    assert summary.resolved == 1
    assert summary.unlinked_active == 0
    assert summary.by_source[0].active == 1

    overview = seeded.service.get_overview(seeded.linked_alert_id)
    assert overview.detection.summary == "错误率达到 18.4%"
    assert overview.detection.facts == {"metric": "http_error_rate", "value": "18.4%"}
    assert len(overview.signals) == 2
    assert overview.signals_truncated is False
    assert overview.correlation.status == "COMPLETED"
    assert overview.correlation.explanation
    assert overview.correlation.incident is not None
    assert overview.correlation.incident.id == seeded.incident_id
    assert [step.title for step in overview.processing_steps] == [
        "告警已接入",
        "重复信号已归并",
        "事故关联已完成",
    ]


def test_keyword_escapes_wildcards_and_missing_alert_is_safe(seeded: SeededAlerts) -> None:
    page = seeded.service.list_alerts(AlertListFilters(query="%_", limit=100, offset=0))
    assert page.items == ()
    with pytest.raises(AlertResourceNotFound):
        seeded.service.get_overview("alt_ffffffffffffffffffffffffffffffff")


def test_overview_caps_signal_history_at_one_hundred(
    seeded: SeededAlerts,
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session:
        for index in range(99):
            signal_id = f"sig_{index + 1000:032x}"
            source_event_id = f"{index + 1000:064x}"
            session.add(
                SignalEventRow(
                    id=signal_id,
                    alert_source_id=seeded.source_a_id,
                    source="alertmanager",
                    source_event_id=source_event_id,
                    event_type="alert.firing",
                    title="payment-api 告警",
                    summary=f"补充检测信号 {index}",
                    severity="critical",
                    service="payment-api",
                    entity_type="SERVICE",
                    entity_key="1" * 64,
                    entity_display_name="payment-api",
                    environment="production",
                    observed_at=NOW - timedelta(seconds=index + 1),
                    received_at=NOW,
                    facts={"metric": "http_error_rate"},
                    payload_fingerprint=f"{index + 1:064x}",
                    created_at=NOW,
                    version=1,
                )
            )
            session.add(
                SignalIntakeResultRow(
                    alert_source_id=seeded.source_a_id,
                    source="alertmanager",
                    source_event_id=source_event_id,
                    command_fingerprint=f"{index + 2:064x}",
                    signal_event_id=signal_id,
                    alert_id=seeded.linked_alert_id,
                    outcome="updated",
                    created_at=NOW,
                )
            )
        session.commit()

    overview = seeded.service.get_overview(seeded.linked_alert_id)
    assert len(overview.signals) == 100
    assert overview.signals_truncated is True
