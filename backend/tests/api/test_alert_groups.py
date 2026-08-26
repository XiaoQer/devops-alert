from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from secrets import token_urlsafe

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from incident_intelligence.ids import new_id
from incident_intelligence.main import create_app
from incident_intelligence.persistence.models import AlertGroupRow, ServiceCatalogEntryRow
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_group_correlation import AlertGroupCorrelationService
from incident_intelligence.services.alert_group_correlation_jobs import (
    AlertGroupCorrelationJobService,
)
from incident_intelligence.services.alert_grouping import AlertGroupingService
from incident_intelligence.services.alert_grouping_jobs import AlertGroupingJobService
from incident_intelligence.services.signal_intake import SignalIntakeService
from incident_intelligence.settings import Settings
from tests.integration.services.test_alert_group_correlation_service import command

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Context:
    client: TestClient
    headers: dict[str, str]


@pytest.fixture
def context(migrated_engine: Engine) -> Context:
    token = token_urlsafe(32)
    app = create_app(
        Settings(
            database_url="mysql+pymysql://test-client@127.0.0.1/unused",
            api_token=SecretStr(token),
            alertmanager_token=SecretStr(token_urlsafe(32)),
            cloudevents_token=SecretStr(token_urlsafe(32)),
            correlation_runner_enabled=False,
        ),
        engine=migrated_engine,
    )
    return Context(
        client=TestClient(app),
        headers={"Authorization": f"Bearer {token}"},
    )


def test_alert_group_endpoints_require_control_plane_token(context: Context) -> None:
    for path in (
        "/api/v1/alert-groups",
        "/api/v1/alert-groups/summary?window=24h",
        "/api/v1/alert-groups/agr_00000000000000000000000000000000/overview",
        "/api/v1/alert-groups/agr_00000000000000000000000000000000/alerts",
    ):
        response = context.client.get(path)
        assert response.status_code == 401
        assert response.json()["code"] == "authentication_required"


def test_empty_group_reads_are_bounded_and_invalid_ids_are_hidden(context: Context) -> None:
    page = context.client.get(
        "/api/v1/alert-groups?limit=100&offset=10000", headers=context.headers
    )
    assert page.status_code == 200
    assert page.json() == {"items": [], "total": 0, "limit": 100, "offset": 10000}

    summary = context.client.get("/api/v1/alert-groups/summary?window=24h", headers=context.headers)
    assert summary.status_code == 200
    assert set(summary.json()) == {
        "window",
        "active_groups",
        "severe_active_groups",
        "active_alerts",
        "storm_groups",
        "resolved_groups",
        "compression_ratio",
        "peak_rate_per_minute",
        "pending_group_jobs",
        "calculated_at",
    }

    for suffix in ("not-an-id/overview", "agr_" + "0" * 32 + "/overview"):
        response = context.client.get(f"/api/v1/alert-groups/{suffix}", headers=context.headers)
        assert response.status_code == 404
        assert response.json()["code"] == "resource_not_found"

    assert (
        context.client.get("/api/v1/alert-groups?limit=101", headers=context.headers).status_code
        == 422
    )
    assert (
        context.client.get("/api/v1/alert-groups?offset=10001", headers=context.headers).status_code
        == 422
    )


def test_real_storm_is_available_through_group_and_incident_pages(
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
        "req-api-storm",
    )
    grouping_jobs = AlertGroupingJobService(uow_factory=uow_factory)
    grouping = AlertGroupingService(uow_factory=uow_factory, clock=lambda: NOW)
    while leases := grouping_jobs.claim_batch("grouping", NOW, limit=50, lease_seconds=30):
        for lease in leases:
            grouping.process(lease)
    correlation_jobs = AlertGroupCorrelationJobService(uow_factory=uow_factory)
    correlation = AlertGroupCorrelationService(uow_factory=uow_factory, clock=lambda: NOW)
    correlation.process(
        correlation_jobs.claim_batch("correlation", NOW, limit=1, lease_seconds=30)[0]
    )
    with session_factory() as session:
        group = session.scalar(select(AlertGroupRow))
    assert group is not None and group.incident_id is not None

    token = token_urlsafe(32)
    app = create_app(
        Settings(
            database_url="mysql+pymysql://test-client@127.0.0.1/unused",
            api_token=SecretStr(token),
            alertmanager_token=SecretStr(token_urlsafe(32)),
            cloudevents_token=SecretStr(token_urlsafe(32)),
            correlation_runner_enabled=False,
        ),
        engine=migrated_engine,
    )
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        group_page = client.get(
            "/api/v1/alert-groups?storm_state=STORM&incident_linked=true",
            headers=headers,
        )
        second_member_page = client.get(
            f"/api/v1/alert-groups/{group.id}/alerts?limit=100&offset=100",
            headers=headers,
        )
        overview = client.get(f"/api/v1/alert-groups/{group.id}/overview", headers=headers)
        incident_groups = client.get(
            f"/api/v1/incidents/{group.incident_id}/alert-groups?limit=20&offset=0",
            headers=headers,
        )

    assert group_page.status_code == 200
    assert group_page.json()["total"] == 1
    assert group_page.json()["items"][0]["total_count"] == 101
    assert second_member_page.status_code == 200
    assert second_member_page.json()["total"] == 101
    assert len(second_member_page.json()["items"]) == 1
    assert overview.status_code == 200
    assert len(overview.json()["impacted_resources"]) == 20
    assert incident_groups.status_code == 200
    assert incident_groups.json()["total"] == 1
    safe_text = " ".join(
        (group_page.text, second_member_page.text, overview.text, incident_groups.text)
    ).casefold()
    for forbidden in ("scenario_id", "experiment_id", "webhook", "bearer "):
        assert forbidden not in safe_text
