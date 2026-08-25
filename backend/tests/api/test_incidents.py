from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from secrets import token_urlsafe

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.signal_intake import SignalCommand
from incident_intelligence.main import create_app
from incident_intelligence.persistence.models import AuditEventRow, IncidentRow
from incident_intelligence.persistence.session import make_session_factory
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.catalog import CreateServiceCommand, ServiceCatalogService
from incident_intelligence.services.correlation import CorrelationService
from incident_intelligence.services.correlation_jobs import CorrelationJobService
from incident_intelligence.services.signal_intake import SignalIntakeService
from incident_intelligence.settings import Settings

NOW = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)


@dataclass(frozen=True)
class IncidentApiContext:
    client: TestClient
    manual_headers: dict[str, str]
    alertmanager_headers: dict[str, str]
    catalog: ServiceCatalogService
    intake: SignalIntakeService
    jobs: CorrelationJobService
    correlation: CorrelationService
    session_factory: sessionmaker[Session]


@pytest.fixture
def context(migrated_engine: Engine) -> Iterator[IncidentApiContext]:
    manual_token = token_urlsafe(32)
    alertmanager_token = token_urlsafe(32)
    settings = Settings(
        database_url="mysql+pymysql://test-client@127.0.0.1/unused",
        api_token=SecretStr(manual_token),
        alertmanager_token=SecretStr(alertmanager_token),
        cloudevents_token=SecretStr(token_urlsafe(32)),
        correlation_runner_enabled=False,
    )
    app = create_app(settings, engine=migrated_engine)
    session_factory = make_session_factory(migrated_engine)
    uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
    with TestClient(app) as client:
        yield IncidentApiContext(
            client=client,
            manual_headers={"Authorization": f"Bearer {manual_token}"},
            alertmanager_headers={"Authorization": f"Bearer {alertmanager_token}"},
            catalog=ServiceCatalogService(uow_factory=uow_factory, clock=lambda: NOW),
            intake=SignalIntakeService(uow_factory=uow_factory, clock=lambda: NOW),
            jobs=CorrelationJobService(uow_factory=uow_factory),
            correlation=CorrelationService(uow_factory=uow_factory, clock=lambda: NOW),
            session_factory=session_factory,
        )


def seed_linked_incident(context: IncidentApiContext) -> str:
    context.catalog.create_service(
        CreateServiceCommand(
            service="payment-api", environment="production", owner_team="支付平台组"
        ),
        actor="manual-api-client",
        request_id="req-catalog",
    )
    incident_id: str | None = None
    for index, event_at in enumerate((NOW, NOW + timedelta(minutes=1)), start=1):
        result = context.intake.submit_batch(
            [
                SignalCommand(
                    source="alertmanager",
                    source_instance="a" * 64,
                    source_event_id=f"{index:064x}",
                    source_alert_key=f"payment-alert-{index}",
                    event_type="alert.firing",
                    event_at=event_at,
                    episode_started_at=event_at,
                    title=f"支付告警 {index}",
                    summary="只用于真实聚合测试",
                    severity="high" if index == 1 else "critical",
                    service="payment-api",
                    environment="production",
                    facts={"symptom": "errors"},
                )
            ],
            actor="alertmanager-adapter",
            request_id=f"req-alert-{index}",
        )
        alert_id = result.items[0].alert_id
        assert alert_id is not None
        lease = context.jobs.claim_batch(
            "incident-api-runner", event_at, limit=1, lease_seconds=30
        )[0]
        correlation = context.correlation.process(lease)
        incident_id = correlation.incident_id
    assert incident_id is not None
    return incident_id


def test_list_and_overview_return_real_bounded_aggregate(
    context: IncidentApiContext,
) -> None:
    incident_id = seed_linked_incident(context)

    listed = context.client.get(
        "/api/v1/incidents?environment=production&query=payment&limit=20&offset=0",
        headers=context.manual_headers,
    )
    detail = context.client.get(
        f"/api/v1/incidents/{incident_id}/overview",
        headers=context.manual_headers,
    )

    assert listed.status_code == 200
    assert set(listed.json()) == {"items", "total", "limit", "offset"}
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["alert_count"] == 2
    assert listed.json()["items"][0]["owner_team"] == "支付平台组"
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == incident_id
    assert len(body["alerts"]) == 2
    assert body["correlation"]["rule_version"] == "correlation.v1"
    assert body["correlation"]["explanation"] == "窗口内只有一个同服务事故，已自动关联。"  # noqa: RUF001
    assert [event["kind"] for event in body["timeline"]][:2] == [
        "incident_created",
        "alert_linked",
    ]
    assert "summary" not in detail.text
    assert "facts" not in detail.text
    assert "source_event_id" not in detail.text


def test_claim_is_persisted_idempotent_and_audited_once(
    context: IncidentApiContext,
) -> None:
    incident_id = seed_linked_incident(context)

    first = context.client.post(
        f"/api/v1/incidents/{incident_id}/claim", headers=context.manual_headers
    )
    replay = context.client.post(
        f"/api/v1/incidents/{incident_id}/claim", headers=context.manual_headers
    )

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["assignee"] == "manual-api-client"
    with context.session_factory() as session:
        incident = session.get(IncidentRow, incident_id)
        assert incident is not None
        assert incident.assignee == "manual-api-client"
        audits = tuple(
            session.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.resource_id == incident_id,
                    AuditEventRow.action == "incident.claimed",
                )
            )
        )
        assert len(audits) == 1
        assert audits[0].details == {"reason_code": "manual_claim_requested"}


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/incidents",
        "/api/v1/incidents/inc_00000000000000000000000000000000/overview",
        "/api/v1/incidents/inc_00000000000000000000000000000000/claim",
    ],
)
def test_incident_center_rejects_non_manual_credentials(
    context: IncidentApiContext,
    path: str,
) -> None:
    method = context.client.post if path.endswith("/claim") else context.client.get
    response = method(path, headers=context.alertmanager_headers)
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


def test_incident_list_and_ids_are_bounded(context: IncidentApiContext) -> None:
    invalid_limit = context.client.get(
        "/api/v1/incidents?limit=101", headers=context.manual_headers
    )
    invalid_query = context.client.get(
        f"/api/v1/incidents?query={'x' * 101}", headers=context.manual_headers
    )
    missing = context.client.get(
        "/api/v1/incidents/not-an-id/overview", headers=context.manual_headers
    )
    assert invalid_limit.status_code == 422
    assert invalid_query.status_code == 422
    assert missing.status_code == 404
    assert missing.json()["code"] == "resource_not_found"
