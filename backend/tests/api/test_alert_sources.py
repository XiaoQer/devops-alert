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
class AlertSourceApiContext:
    client: TestClient
    manual_headers: dict[str, str]
    alertmanager_headers: dict[str, str]


@pytest.fixture
def context(migrated_engine: Engine) -> Iterator[AlertSourceApiContext]:
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
    with TestClient(app) as client:
        yield AlertSourceApiContext(
            client=client,
            manual_headers={"Authorization": f"Bearer {manual_token}"},
            alertmanager_headers={"Authorization": f"Bearer {alertmanager_token}"},
        )


def create_source(context: AlertSourceApiContext, *, key: str = "create-1"):
    return context.client.post(
        "/api/v1/alert-sources",
        headers={**context.manual_headers, "Idempotency-Key": key},
        json={
            "name": "生产 Alertmanager",
            "source_type": "ALERTMANAGER",
            "environment": "production",
            "environment_name": "生产环境",
        },
    )


def test_create_source_requires_environment(context: AlertSourceApiContext) -> None:
    response = context.client.post(
        "/api/v1/alert-sources",
        headers={**context.manual_headers, "Idempotency-Key": "missing-environment"},
        json={"name": "未配置环境的来源", "source_type": "ALERTMANAGER"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


@pytest.mark.parametrize("credential", ["none", "alertmanager"])
def test_only_manual_actor_can_manage_sources(
    context: AlertSourceApiContext,
    credential: str,
) -> None:
    headers = {
        "none": {"Idempotency-Key": "unauthorized"},
        "alertmanager": {
            **context.alertmanager_headers,
            "Idempotency-Key": "unauthorized",
        },
    }[credential]
    response = context.client.post(
        "/api/v1/alert-sources",
        headers=headers,
        json={"name": "禁止创建", "source_type": "ALERTMANAGER"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


def test_create_replay_list_and_detail_never_reveal_historical_secret(
    context: AlertSourceApiContext,
) -> None:
    created = create_source(context)
    replayed = create_source(context)
    assert created.status_code == 201
    assert replayed.status_code == 200
    first = created.json()
    second = replayed.json()
    assert first["token"].startswith(f"iisrc_{first['credential_id']}.")
    assert first["secret_retrievable"] is True
    assert first["source"]["environment"] == "production"
    assert first["source"]["environment_name"] == "生产环境"
    assert first["source"]["environment_configured"] is True
    assert second["token"] is None
    assert second["secret_retrievable"] is False

    source_id = first["source"]["id"]
    listed = context.client.get(
        "/api/v1/alert-sources?source_type=ALERTMANAGER&limit=100",
        headers=context.manual_headers,
    )
    detail = context.client.get(
        f"/api/v1/alert-sources/{source_id}", headers=context.manual_headers
    )
    receipts = context.client.get(
        f"/api/v1/alert-sources/{source_id}/receipts?limit=100",
        headers=context.manual_headers,
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert detail.status_code == 200
    assert receipts.status_code == 200
    assert receipts.json()["items"] == []
    assert receipts.json()["total"] == 0
    assert detail.json()["credentials"][0]["id"] == first["credential_id"]
    assert "token" not in detail.text
    assert "digest" not in detail.text
    assert first["token"] not in listed.text


def test_update_rotate_revoke_and_errors_have_stable_contract(
    context: AlertSourceApiContext,
) -> None:
    created = create_source(context).json()
    source_id = created["source"]["id"]
    old_credential_id = created["credential_id"]
    stale = context.client.patch(
        f"/api/v1/alert-sources/{source_id}",
        headers={**context.manual_headers, "Idempotency-Key": "stale"},
        json={"expected_version": 99, "name": "旧版本"},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "alert_source_version_conflict"

    rotated = context.client.post(
        f"/api/v1/alert-sources/{source_id}/credentials/rotate",
        headers={**context.manual_headers, "Idempotency-Key": "rotate"},
        json={"expected_version": 1},
    )
    assert rotated.status_code == 200
    assert rotated.json()["source"]["version"] == 2
    assert rotated.json()["token"] is not None

    revoked = context.client.post(
        f"/api/v1/alert-sources/{source_id}/credentials/{old_credential_id}/revoke",
        headers={**context.manual_headers, "Idempotency-Key": "revoke"},
        json={"expected_version": 2},
    )
    assert revoked.status_code == 200
    assert revoked.json()["source"]["version"] == 3
    assert revoked.json()["token"] is None


def test_disabled_source_can_update_its_environment(
    context: AlertSourceApiContext,
) -> None:
    created = create_source(context, key="environment-create").json()["source"]
    source_id = created["id"]
    disabled = context.client.patch(
        f"/api/v1/alert-sources/{source_id}",
        headers={**context.manual_headers, "Idempotency-Key": "environment-disable"},
        json={"expected_version": 1, "state": "DISABLED"},
    )
    assert disabled.status_code == 200

    updated = context.client.patch(
        f"/api/v1/alert-sources/{source_id}",
        headers={**context.manual_headers, "Idempotency-Key": "environment-update"},
        json={
            "expected_version": 2,
            "environment": "pre-production",
            "environment_name": "预生产环境",
        },
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["source"]["environment"] == "pre-production"
    assert updated.json()["source"]["environment_name"] == "预生产环境"
    assert updated.json()["source"]["environment_configured"] is True


def test_invalid_requests_and_system_source_mutation_are_safe(
    context: AlertSourceApiContext,
) -> None:
    missing_key = context.client.post(
        "/api/v1/alert-sources",
        headers=context.manual_headers,
        json={"name": "无幂等键", "source_type": "ALERTMANAGER"},
    )
    manual_type = context.client.post(
        "/api/v1/alert-sources",
        headers={**context.manual_headers, "Idempotency-Key": "manual-type"},
        json={"name": "人工来源", "source_type": "MANUAL"},
    )
    system_update = context.client.patch(
        "/api/v1/alert-sources/src_00000000000000000000000000000002",
        headers={**context.manual_headers, "Idempotency-Key": "system-update"},
        json={"expected_version": 1, "name": "禁止修改"},
    )
    assert missing_key.status_code == 400
    assert manual_type.status_code == 422
    assert system_update.status_code == 409
    assert system_update.json()["code"] == "system_managed_source_read_only"


def test_system_compatibility_source_allows_initial_environment_configuration(
    context: AlertSourceApiContext,
) -> None:
    response = context.client.patch(
        "/api/v1/alert-sources/src_00000000000000000000000000000002",
        headers={**context.manual_headers, "Idempotency-Key": "system-environment"},
        json={
            "expected_version": 1,
            "environment": "production",
            "environment_name": "生产环境",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["source"]["environment"] == "production"
    assert response.json()["source"]["environment_configured"] is True
