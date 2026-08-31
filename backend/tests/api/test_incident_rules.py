from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from secrets import token_urlsafe

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.engine import Engine

from incident_intelligence.main import create_app
from incident_intelligence.settings import Settings


@dataclass(frozen=True)
class RuleApiContext:
    client: TestClient
    headers: dict[str, str]


@pytest.fixture
def context(migrated_engine: Engine) -> Iterator[RuleApiContext]:
    manual_token = token_urlsafe(32)
    settings = Settings(
        database_url="mysql+pymysql://test-client@127.0.0.1/unused",
        api_token=SecretStr(manual_token),
        alertmanager_token=SecretStr(token_urlsafe(32)),
        cloudevents_token=SecretStr(token_urlsafe(32)),
    )
    with TestClient(create_app(settings, engine=migrated_engine)) as client:
        yield RuleApiContext(
            client=client,
            headers={"Authorization": f"Bearer {manual_token}"},
        )


def _payload(name: str = "支付链路异常") -> dict[str, object]:
    return {
        "name": name,
        "description": "识别支付服务短时间内的多类告警",
        "config": {
            "environment": "production",
            "alert_source_ids": [],
            "services": ["checkout"],
            "group_by": "SERVICE",
            "window_minutes": 5,
            "conditions": [
                {"type": "DISTINCT_ALERT_NAMES_GTE", "threshold": 2},
                {"type": "MAX_SEVERITY_AT_LEAST", "severity": "high"},
            ],
        },
    }


def _create(context: RuleApiContext, key: str = "create-1"):
    return context.client.post(
        "/api/v1/incident-rules",
        headers={**context.headers, "Idempotency-Key": key},
        json=_payload(),
    )


def test_create_list_get_dry_run_publish_disable_and_copy(context: RuleApiContext) -> None:
    created_response = _create(context)
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    rule = created["rule"]
    assert created["replayed"] is False
    assert rule["state"] == "DRAFT"
    assert rule["publishable"] is False
    assert "按同一服务分组" in rule["summary"]

    replayed = _create(context)
    assert replayed.status_code == 200
    assert replayed.json()["rule"]["id"] == rule["id"]
    assert replayed.json()["replayed"] is True

    listed = context.client.get("/api/v1/incident-rules?state=DRAFT", headers=context.headers)
    detail = context.client.get(f"/api/v1/incident-rules/{rule['id']}", headers=context.headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert detail.status_code == 200

    dry_run = context.client.post(
        f"/api/v1/incident-rules/{rule['id']}/dry-runs",
        headers=context.headers,
        json={"expected_version": rule["version"], "history_hours": 6},
    )
    assert dry_run.status_code == 200, dry_run.text
    assert dry_run.json()["scanned_alert_count"] == 0
    assert dry_run.json()["match_count"] == 0
    assert dry_run.json()["rule"]["publishable"] is True

    published = context.client.post(
        f"/api/v1/incident-rules/{rule['id']}/publish",
        headers={**context.headers, "Idempotency-Key": "publish-1"},
        json={"expected_version": rule["version"]},
    )
    assert published.status_code == 200, published.text
    published_rule = published.json()["rule"]
    assert published_rule["state"] == "PUBLISHED"

    copied = context.client.post(
        f"/api/v1/incident-rules/{rule['id']}/copies",
        headers={**context.headers, "Idempotency-Key": "copy-1"},
        json={"name": "支付链路异常副本"},
    )
    assert copied.status_code == 201, copied.text
    assert copied.json()["rule"]["state"] == "DRAFT"

    disabled = context.client.post(
        f"/api/v1/incident-rules/{rule['id']}/disable",
        headers={**context.headers, "Idempotency-Key": "disable-1"},
        json={"expected_version": published_rule["version"]},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["rule"]["state"] == "DISABLED"


def test_update_delete_validation_auth_and_version_conflict(context: RuleApiContext) -> None:
    unauthorized = context.client.get("/api/v1/incident-rules")
    invalid = context.client.post(
        "/api/v1/incident-rules",
        headers={**context.headers, "Idempotency-Key": "invalid"},
        json={**_payload(), "config": {**_payload()["config"], "window_minutes": 61}},
    )
    assert unauthorized.status_code == 401
    assert invalid.status_code == 422

    rule = _create(context, key="create-delete").json()["rule"]
    stale = context.client.patch(
        f"/api/v1/incident-rules/{rule['id']}",
        headers={**context.headers, "Idempotency-Key": "stale"},
        json={**_payload(), "expected_version": 99},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "incident_rule_version_conflict"

    deleted = context.client.request(
        "DELETE",
        f"/api/v1/incident-rules/{rule['id']}",
        headers={**context.headers, "Idempotency-Key": "delete-1"},
        json={"expected_version": rule["version"]},
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert (
        context.client.get(
            f"/api/v1/incident-rules/{rule['id']}", headers=context.headers
        ).status_code
        == 404
    )
