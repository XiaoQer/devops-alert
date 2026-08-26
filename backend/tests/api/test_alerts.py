from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.engine import Engine

from incident_intelligence.main import create_app
from incident_intelligence.settings import Settings


@dataclass(frozen=True)
class AlertApiContext:
    client: TestClient
    manual_headers: dict[str, str]
    alert_id: str
    incident_id: str
    source_id: str


@pytest.fixture
def context(migrated_engine: Engine) -> Iterator[AlertApiContext]:
    manual_token = token_urlsafe(32)
    app = create_app(
        Settings(
            database_url="mysql+pymysql://test-client@127.0.0.1/unused",
            api_token=SecretStr(manual_token),
            alertmanager_token=SecretStr(token_urlsafe(32)),
            cloudevents_token=SecretStr(token_urlsafe(32)),
            correlation_runner_enabled=False,
        ),
        engine=migrated_engine,
    )
    headers = {"Authorization": f"Bearer {manual_token}"}
    with TestClient(app) as client:
        source_response = client.post(
            "/api/v1/alert-sources",
            headers={**headers, "Idempotency-Key": "create-alert-api-source"},
            json={"name": "告警中心来源", "source_type": "ALERTMANAGER"},
        )
        source = source_response.json()
        client.post(
            "/api/v1/catalog/services",
            headers=headers,
            json={
                "service": "payment-api",
                "environment": "production",
                "owner_team": "payments",
            },
        )
        now = datetime.now(UTC)
        payload = {
            "version": "4",
            "groupKey": '{}:{alertname="PaymentHighErrorRate"}',
            "truncatedAlerts": 0,
            "status": "firing",
            "receiver": "incident-intelligence",
            "groupLabels": {"alertname": "PaymentHighErrorRate"},
            "commonLabels": {},
            "commonAnnotations": {},
            "externalURL": "https://alertmanager.example.com",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "PaymentHighErrorRate",
                        "service": "payment-api",
                        "environment": "production",
                        "severity": "high",
                        "value": "18.4%",
                    },
                    "annotations": {
                        "summary": "支付错误率升高",
                        "description": "实际错误率达到 18.4%",
                    },
                    "startsAt": (now - timedelta(minutes=1)).isoformat(),
                    "endsAt": "0001-01-01T00:00:00Z",
                    "generatorURL": "https://prometheus.example.com/graph",
                    "fingerprint": "alert-center-api",
                }
            ],
        }
        intake = client.post(
            f"/api/v1/intake/alertmanager/{source['source']['id']}",
            headers={"Authorization": f"Bearer {source['token']}"},
            json=payload,
        )
        assert intake.status_code == 202
        alert_id = intake.json()["items"][0]["alert_id"]
        grouping_lease = app.state.alert_grouping_job_service.claim_batch(
            "alert-grouping-api-test",
            datetime.now(UTC),
            limit=1,
            lease_seconds=30,
        )[0]
        app.state.alert_grouping_service.process(grouping_lease)
        lease = app.state.alert_group_correlation_job_service.claim_batch(
            "alert-api-test", datetime.now(UTC), limit=1, lease_seconds=30
        )[0]
        result = app.state.alert_group_correlation_service.process(lease)
        assert result.incident_id is not None
        yield AlertApiContext(
            client=client,
            manual_headers=headers,
            alert_id=alert_id,
            incident_id=result.incident_id,
            source_id=source["source"]["id"],
        )


def test_alert_list_summary_and_overview_contract(context: AlertApiContext) -> None:
    listed = context.client.get(
        f"/api/v1/alerts?state=ACTIVE&severity=high&alert_source_id={context.source_id}"
        "&service=payment-api&environment=production&incident_linked=true&limit=100",
        headers=context.manual_headers,
    )
    summary = context.client.get(
        "/api/v1/alerts/summary?window=24h", headers=context.manual_headers
    )
    overview = context.client.get(
        f"/api/v1/alerts/{context.alert_id}/overview", headers=context.manual_headers
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["incident_id"] == context.incident_id
    assert summary.status_code == 200
    assert summary.json()["active"] == 1
    assert overview.status_code == 200
    body = overview.json()
    assert body["detection"]["summary"] == "实际错误率达到 18.4%"
    assert body["source"]["name"] == "告警中心来源"
    assert body["correlation"]["incident"]["id"] == context.incident_id
    assert body["group"]["id"].startswith("agr_")
    assert body["group"]["total_count"] == 1
    assert body["group"]["incident_id"] == context.incident_id
    assert len(body["processing_steps"]) == 3
    assert "token" not in overview.text.casefold()
    assert "digest" not in overview.text.casefold()


def test_alert_reads_require_manual_auth_and_validate_filters(context: AlertApiContext) -> None:
    unauthenticated = context.client.get("/api/v1/alerts")
    invalid_limit = context.client.get("/api/v1/alerts?limit=101", headers=context.manual_headers)
    invalid_window = context.client.get(
        "/api/v1/alerts/summary?window=30d", headers=context.manual_headers
    )
    invalid_range = context.client.get(
        "/api/v1/alerts?observed_from=2026-08-27T00:00:00Z&observed_to=2026-08-26T00:00:00Z",
        headers=context.manual_headers,
    )
    assert unauthenticated.status_code == 401
    assert invalid_limit.status_code == 422
    assert invalid_window.status_code == 422
    assert invalid_range.status_code == 422


def test_missing_overview_and_existing_alert_resource_remain_compatible(
    context: AlertApiContext,
) -> None:
    missing = context.client.get(
        "/api/v1/alerts/alt_ffffffffffffffffffffffffffffffff/overview",
        headers=context.manual_headers,
    )
    old_resource = context.client.get(
        f"/api/v1/alerts/{context.alert_id}", headers=context.manual_headers
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "resource_not_found"
    assert old_resource.status_code == 200
    assert old_resource.json()["id"] == context.alert_id
