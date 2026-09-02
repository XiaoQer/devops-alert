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
    settings = Settings(
        database_url="mysql+pymysql://test-client@127.0.0.1/unused",
        api_token=SecretStr(token),
        alertmanager_token=SecretStr(token_urlsafe(32)),
        cloudevents_token=SecretStr(token_urlsafe(32)),
    )
    with TestClient(create_app(settings, engine=migrated_engine)) as client:
        yield client, {"Authorization": f"Bearer {token}"}


def test_create_source_returns_readiness_without_secret(
    context: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = context
    payload = {
        "name": "测试 Prometheus",
        "environment": "testing",
        "source_type": "PROMETHEUS",
        "base_url": "http://prometheus:9090",
        "credential_env_key": "II_PROMETHEUS_TOKEN",
        "field_mapping": {"service": "service", "environment": "environment"},
        "verify_tls": True,
        "enabled": True,
    }

    unauthorized = client.post("/api/v1/monitoring-data-sources", json=payload)
    created = client.post(
        "/api/v1/monitoring-data-sources",
        headers=headers,
        json=payload,
    )
    listed = client.get("/api/v1/monitoring-data-sources", headers=headers)

    assert unauthorized.status_code == 401
    assert created.status_code == 201, created.text
    assert created.json()["credential_configured"] is False
    assert "secret" not in created.text.casefold()
    assert listed.status_code == 200
    assert listed.json()["total"] == 1


def test_patch_uses_expected_version(context: tuple[TestClient, dict[str, str]]) -> None:
    client, headers = context
    created = client.post(
        "/api/v1/monitoring-data-sources",
        headers=headers,
        json={
            "name": "测试 Elasticsearch",
            "environment": "testing",
            "source_type": "ELASTICSEARCH",
            "base_url": "http://elasticsearch:9200",
            "credential_env_key": None,
            "field_mapping": {
                "index": "logs-*",
                "timestamp": "@timestamp",
                "service": "service.name",
                "environment": "environment",
                "message": "message",
            },
            "verify_tls": True,
            "enabled": True,
        },
    )
    source = created.json()

    updated = client.patch(
        f"/api/v1/monitoring-data-sources/{source['id']}",
        headers=headers,
        json={
            "name": source["name"],
            "environment": source["environment"],
            "source_type": source["source_type"],
            "base_url": source["base_url"],
            "credential_env_key": source["credential_env_key"],
            "field_mapping": source["field_mapping"],
            "verify_tls": source["verify_tls"],
            "enabled": False,
            "expected_version": 1,
        },
    )
    stale = client.patch(
        f"/api/v1/monitoring-data-sources/{source['id']}",
        headers=headers,
        json={
            "name": source["name"],
            "environment": source["environment"],
            "source_type": source["source_type"],
            "base_url": source["base_url"],
            "credential_env_key": source["credential_env_key"],
            "field_mapping": source["field_mapping"],
            "verify_tls": source["verify_tls"],
            "enabled": False,
            "expected_version": 1,
        },
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    assert stale.status_code == 409
    assert stale.json()["code"] == "monitoring_data_source_version_conflict"
