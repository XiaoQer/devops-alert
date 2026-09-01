from __future__ import annotations

from collections.abc import Iterator
from secrets import token_urlsafe

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.engine import Engine

from incident_intelligence.main import create_app
from incident_intelligence.settings import Settings


@pytest.fixture
def context(migrated_engine: Engine) -> Iterator[tuple[TestClient, dict[str, str]]]:
    token = token_urlsafe(32)
    settings = _settings(
        token,
        feishu_app_id=SecretStr("cli_test"),
        feishu_app_secret=SecretStr("app-secret"),
        feishu_verification_token=SecretStr("verification-token"),
    )
    with TestClient(create_app(settings, engine=migrated_engine)) as client:
        yield client, {"Authorization": f"Bearer {token}"}


def test_route_api_creates_lists_replays_and_updates(
    context: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = context
    payload = {
        "environment": "production",
        "chat_id": "oc_production_incidents",
        "chat_name": "生产事故群",
        "enabled": True,
    }

    empty = client.get("/api/v1/incident-notification-routes", headers=headers)
    created = client.post(
        "/api/v1/incident-notification-routes",
        headers={**headers, "Idempotency-Key": "create-production-route"},
        json=payload,
    )
    replayed = client.post(
        "/api/v1/incident-notification-routes",
        headers={**headers, "Idempotency-Key": "create-production-route"},
        json=payload,
    )

    assert empty.status_code == 200
    assert empty.json() == {
        "items": [],
        "total": 0,
        "feishu_capability": {
            "configured": True,
            "missing_environment_keys": [],
        },
    }
    assert created.status_code == 201, created.text
    route = created.json()["route"]
    assert route["environment"] == "production"
    assert route["enabled"] is True
    assert replayed.status_code == 200
    assert replayed.json()["replayed"] is True
    assert replayed.json()["route"]["id"] == route["id"]

    updated = client.patch(
        f"/api/v1/incident-notification-routes/{route['id']}",
        headers={**headers, "Idempotency-Key": "disable-production-route"},
        json={**payload, "enabled": False, "expected_version": 1},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["route"]["enabled"] is False
    assert updated.json()["route"]["version"] == 2


def test_enabling_route_requires_complete_feishu_credentials(
    migrated_engine: Engine,
) -> None:
    token = token_urlsafe(32)
    with TestClient(create_app(_settings(token), engine=migrated_engine)) as client:
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "route-1"}
        enabled = client.post(
            "/api/v1/incident-notification-routes",
            headers=headers,
            json={
                "environment": "production",
                "chat_id": "oc_production_incidents",
                "chat_name": "生产事故群",
                "enabled": True,
            },
        )
        disabled = client.post(
            "/api/v1/incident-notification-routes",
            headers={**headers, "Idempotency-Key": "route-disabled"},
            json={
                "environment": "production",
                "chat_id": "oc_production_incidents",
                "chat_name": "生产事故群",
                "enabled": False,
            },
        )

    assert enabled.status_code == 409
    assert enabled.json()["code"] == "feishu_credentials_incomplete"
    assert "II_FEISHU_APP_SECRET" in enabled.json()["message"]
    assert disabled.status_code == 201


def _settings(token: str, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "mysql+pymysql://test-client@127.0.0.1/unused",
        "api_token": SecretStr(token),
        "alertmanager_token": SecretStr(token_urlsafe(32)),
        "cloudevents_token": SecretStr(token_urlsafe(32)),
    }
    values.update(overrides)
    return Settings(**values)
