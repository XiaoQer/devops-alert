from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.signal_intake import SignalCommand
from incident_intelligence.persistence.correlation_repository import CorrelationRepository
from incident_intelligence.persistence.models import (
    CorrelationDecisionRow,
    CorrelationJobRow,
    DiagnosisRunRow,
    IncidentAlertLinkRow,
    IncidentRow,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.catalog import (
    CreateDependencyCommand,
    CreateServiceCommand,
    ServiceCatalogService,
)
from incident_intelligence.services.correlation import CorrelationService
from incident_intelligence.services.correlation_jobs import CorrelationJobService
from incident_intelligence.services.signal_intake import SignalIntakeService

NOW = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)
SOURCE_INSTANCE = "a" * 64


@pytest.fixture
def session_factory(migrated_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def services(
    session_factory: sessionmaker[Session],
) -> tuple[ServiceCatalogService, SignalIntakeService, CorrelationJobService, CorrelationService]:
    uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
    return (
        ServiceCatalogService(uow_factory=uow_factory, clock=lambda: NOW),
        SignalIntakeService(uow_factory=uow_factory, clock=lambda: NOW),
        CorrelationJobService(uow_factory=uow_factory),
        CorrelationService(uow_factory=uow_factory, clock=lambda: NOW),
    )


def command(
    *,
    event_number: int,
    alert_key: str,
    severity: str = "high",
    service: str = "payment-api",
    environment: str = "production",
    event_type: str = "alert.firing",
    event_at: datetime = NOW,
    symptom: str = "errors",
) -> SignalCommand:
    return SignalCommand.model_validate(
        {
            "source": "alertmanager",
            "source_instance": SOURCE_INSTANCE,
            "source_event_id": f"{event_number:064x}",
            "source_alert_key": alert_key,
            "event_type": event_type,
            "event_at": event_at,
            "episode_started_at": NOW - timedelta(minutes=1),
            "title": f"告警 {alert_key}",
            "summary": "外部监控信号",
            "severity": severity,
            "service": service,
            "environment": environment,
            "facts": {"symptom": symptom},
        }
    )


def test_first_alert_creates_incident_and_second_exact_alert_links_without_diagnosis(
    services: tuple[
        ServiceCatalogService,
        SignalIntakeService,
        CorrelationJobService,
        CorrelationService,
    ],
    session_factory: sessionmaker[Session],
) -> None:
    catalog, intake, jobs, correlation = services
    catalog.create_service(
        CreateServiceCommand(
            service="payment-api",
            environment="production",
            owner_team="payments",
        ),
        actor="manual-api-client",
        request_id="req-catalog",
    )

    first = intake.submit_batch(
        [command(event_number=1, alert_key="payment-errors-a")],
        actor="alertmanager-adapter",
        request_id="req-signal-1",
    )
    first_lease = jobs.claim_batch("runner-1", NOW, limit=1, lease_seconds=30)[0]
    first_decision = correlation.process(first_lease)

    second = intake.submit_batch(
        [
            command(
                event_number=2,
                alert_key="payment-errors-b",
                severity="critical",
                event_at=NOW + timedelta(minutes=15),
            )
        ],
        actor="alertmanager-adapter",
        request_id="req-signal-2",
    )
    second_lease = jobs.claim_batch(
        "runner-1", NOW + timedelta(minutes=15), limit=1, lease_seconds=30
    )[0]
    second_decision = correlation.process(second_lease)

    assert first_decision.outcome == "CREATED_NO_MATCH"
    assert second_decision.outcome == "LINKED_EXACT_SERVICE"
    assert second_decision.incident_id == first_decision.incident_id
    assert first.items[0].alert_id != second.items[0].alert_id

    with session_factory() as session:
        incident = session.get(IncidentRow, first_decision.incident_id)
        links = list(
            session.scalars(
                select(IncidentAlertLinkRow).order_by(IncidentAlertLinkRow.linked_at)
            )
        )
        decisions = list(
            session.scalars(select(CorrelationDecisionRow).order_by(CorrelationDecisionRow.created_at))
        )
        job_states = list(session.scalars(select(CorrelationJobRow.state)))
        diagnosis_count = session.scalar(select(func.count()).select_from(DiagnosisRunRow))

    assert incident is not None
    assert incident.severity == "critical"
    assert incident.version == 2
    assert {link.relation for link in links} == {"PRIMARY", "RELATED"}
    assert len(decisions) == 2
    assert all(
        set(decision.facts)
        <= {
            "service",
            "environment",
            "severity",
            "symptom",
            "window_seconds",
            "candidate_count",
        }
        for decision in decisions
    )
    assert job_states == ["SUCCEEDED", "SUCCEEDED"]
    assert diagnosis_count == 0


def test_ineligible_alert_only_records_decision(
    services: tuple[
        ServiceCatalogService,
        SignalIntakeService,
        CorrelationJobService,
        CorrelationService,
    ],
    session_factory: sessionmaker[Session],
) -> None:
    _, intake, jobs, correlation = services
    intake.submit_batch(
        [command(event_number=10, alert_key="medium-alert", severity="medium")],
        actor="alertmanager-adapter",
        request_id="req-medium",
    )

    result = correlation.process(jobs.claim_batch("runner", NOW, limit=1, lease_seconds=30)[0])

    assert result.outcome == "REJECTED_INELIGIBLE"
    assert result.incident_id is None
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(IncidentRow)) == 0
        assert session.scalar(select(func.count()).select_from(IncidentAlertLinkRow)) == 0
        assert session.scalar(select(func.count()).select_from(CorrelationDecisionRow)) == 1
        assert session.scalar(select(func.count()).select_from(DiagnosisRunRow)) == 0


def test_old_alert_version_is_superseded_without_creating_incident(
    services: tuple[
        ServiceCatalogService,
        SignalIntakeService,
        CorrelationJobService,
        CorrelationService,
    ],
    session_factory: sessionmaker[Session],
) -> None:
    catalog, intake, jobs, correlation = services
    catalog.create_service(
        CreateServiceCommand(
            service="payment-api", environment="production", owner_team="payments"
        ),
        actor="manual-api-client",
        request_id="req-catalog",
    )
    intake.submit_batch(
        [command(event_number=20, alert_key="versioned-alert")],
        actor="alertmanager-adapter",
        request_id="req-v1",
    )
    old_lease = jobs.claim_batch("runner", NOW, limit=1, lease_seconds=30)[0]
    intake.submit_batch(
        [
            command(
                event_number=21,
                alert_key="versioned-alert",
                event_at=NOW + timedelta(minutes=1),
            )
        ],
        actor="alertmanager-adapter",
        request_id="req-v2",
    )

    result = correlation.process(old_lease)

    assert result.outcome == "SUPERSEDED"
    assert result.incident_id is None
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(IncidentRow)) == 0
        assert session.scalar(select(func.count()).select_from(DiagnosisRunRow)) == 0


def test_resolved_alert_records_resolution_without_closing_incident(
    services: tuple[
        ServiceCatalogService,
        SignalIntakeService,
        CorrelationJobService,
        CorrelationService,
    ],
    session_factory: sessionmaker[Session],
) -> None:
    catalog, intake, jobs, correlation = services
    catalog.create_service(
        CreateServiceCommand(
            service="payment-api", environment="production", owner_team="payments"
        ),
        actor="manual-api-client",
        request_id="req-catalog",
    )
    intake.submit_batch(
        [command(event_number=30, alert_key="resolved-alert")],
        actor="alertmanager-adapter",
        request_id="req-fire",
    )
    opened = correlation.process(jobs.claim_batch("runner", NOW, limit=1, lease_seconds=30)[0])
    intake.submit_batch(
        [
            command(
                event_number=31,
                alert_key="resolved-alert",
                event_type="alert.resolved",
                event_at=NOW + timedelta(minutes=2),
            )
        ],
        actor="alertmanager-adapter",
        request_id="req-resolve",
    )
    resolved = correlation.process(
        jobs.claim_batch(
            "runner", NOW + timedelta(minutes=2), limit=1, lease_seconds=30
        )[0]
    )

    assert resolved.outcome == "RECORDED_RESOLUTION"
    assert resolved.incident_id == opened.incident_id
    with session_factory() as session:
        incident = session.get(IncidentRow, opened.incident_id)
        assert incident is not None
        assert incident.state == "DETECTED"
        assert incident.version == 1
        assert session.scalar(select(func.count()).select_from(IncidentAlertLinkRow)) == 1
        assert session.scalar(select(func.count()).select_from(DiagnosisRunRow)) == 0


def test_dependency_with_same_normalized_symptom_is_candidate_not_auto_merge(
    services: tuple[
        ServiceCatalogService,
        SignalIntakeService,
        CorrelationJobService,
        CorrelationService,
    ],
    session_factory: sessionmaker[Session],
) -> None:
    catalog, intake, jobs, correlation = services
    upstream = catalog.create_service(
        CreateServiceCommand(
            service="database", environment="production", owner_team="data"
        ),
        actor="manual-api-client",
        request_id="req-db",
    )
    payment = catalog.create_service(
        CreateServiceCommand(
            service="payment-api", environment="production", owner_team="payments"
        ),
        actor="manual-api-client",
        request_id="req-payment",
    )
    catalog.create_dependency(
        CreateDependencyCommand(
            caller_service_id=payment.id,
            dependency_service_id=upstream.id,
        ),
        actor="manual-api-client",
        request_id="req-dependency",
    )
    intake.submit_batch(
        [
            command(
                event_number=40,
                alert_key="database-errors",
                service="database",
                symptom="high-error-rate",
            )
        ],
        actor="alertmanager-adapter",
        request_id="req-upstream-alert",
    )
    upstream_result = correlation.process(
        jobs.claim_batch("runner", NOW, limit=1, lease_seconds=30)[0]
    )
    intake.submit_batch(
        [
            command(
                event_number=41,
                alert_key="payment-errors",
                symptom="errors",
                event_at=NOW + timedelta(minutes=5),
            )
        ],
        actor="alertmanager-adapter",
        request_id="req-payment-alert",
    )
    payment_result = correlation.process(
        jobs.claim_batch(
            "runner", NOW + timedelta(minutes=5), limit=1, lease_seconds=30
        )[0]
    )

    assert payment_result.outcome == "CREATED_DEPENDENCY_CANDIDATE"
    assert payment_result.incident_id != upstream_result.incident_id
    with session_factory() as session:
        decision = session.get(CorrelationDecisionRow, payment_result.decision_id)
        assert decision is not None
        assert decision.candidate_incident_ids == [upstream_result.incident_id]
        assert session.scalar(select(func.count()).select_from(IncidentRow)) == 2
        assert session.scalar(select(func.count()).select_from(DiagnosisRunRow)) == 0


def test_write_failure_rolls_back_incident_link_and_decision(
    services: tuple[
        ServiceCatalogService,
        SignalIntakeService,
        CorrelationJobService,
        CorrelationService,
    ],
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog, intake, jobs, correlation = services
    catalog.create_service(
        CreateServiceCommand(
            service="payment-api", environment="production", owner_team="payments"
        ),
        actor="manual-api-client",
        request_id="req-catalog",
    )
    intake.submit_batch(
        [command(event_number=50, alert_key="rollback-alert")],
        actor="alertmanager-adapter",
        request_id="req-alert",
    )
    lease = jobs.claim_batch("runner", NOW, limit=1, lease_seconds=30)[0]

    def fail_link(_: CorrelationRepository, __: IncidentAlertLinkRow) -> None:
        raise RuntimeError("simulated_link_failure")

    monkeypatch.setattr(CorrelationRepository, "add_link", fail_link)
    with pytest.raises(RuntimeError, match="simulated_link_failure"):
        correlation.process(lease)

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(IncidentRow)) == 0
        assert session.scalar(select(func.count()).select_from(IncidentAlertLinkRow)) == 0
        assert session.scalar(select(func.count()).select_from(CorrelationDecisionRow)) == 0
        job = session.get(CorrelationJobRow, lease.id)
        assert job is not None
        assert job.state == "LEASED"


def test_concurrent_same_service_alerts_converge_to_one_incident(
    services: tuple[
        ServiceCatalogService,
        SignalIntakeService,
        CorrelationJobService,
        CorrelationService,
    ],
    session_factory: sessionmaker[Session],
) -> None:
    catalog, intake, jobs, correlation = services
    catalog.create_service(
        CreateServiceCommand(
            service="payment-api", environment="production", owner_team="payments"
        ),
        actor="manual-api-client",
        request_id="req-catalog",
    )
    intake.submit_batch(
        [
            command(event_number=60, alert_key="concurrent-a"),
            command(event_number=61, alert_key="concurrent-b"),
        ],
        actor="alertmanager-adapter",
        request_id="req-concurrent",
    )
    leases = jobs.claim_batch("runner", NOW, limit=2, lease_seconds=30)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(correlation.process, leases))

    assert {result.outcome.value for result in results} == {
        "CREATED_NO_MATCH",
        "LINKED_EXACT_SERVICE",
    }
    assert len({result.incident_id for result in results}) == 1
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(IncidentRow)) == 1
        assert session.scalar(select(func.count()).select_from(IncidentAlertLinkRow)) == 2
        assert session.scalar(select(func.count()).select_from(CorrelationDecisionRow)) == 2
        assert session.scalar(select(func.count()).select_from(DiagnosisRunRow)) == 0
