# ruff: noqa: RUF001

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
class CatalogApiContext:
    client: TestClient
    manual_headers: dict[str, str]
    alertmanager_headers: dict[str, str]
    cloudevents_headers: dict[str, str]


@pytest.fixture
def context(migrated_engine: Engine) -> Iterator[CatalogApiContext]:
    manual_token = token_urlsafe(32)
    alertmanager_token = token_urlsafe(32)
    cloudevents_token = token_urlsafe(32)
    settings = Settings(
        database_url="mysql+pymysql://test-client@127.0.0.1/unused",
        api_token=SecretStr(manual_token),
        alertmanager_token=SecretStr(alertmanager_token),
        cloudevents_token=SecretStr(cloudevents_token),
        correlation_runner_enabled=False,
    )
    app = create_app(settings, engine=migrated_engine)
    with TestClient(app) as client:
        yield CatalogApiContext(
            client=client,
            manual_headers={"Authorization": f"Bearer {manual_token}"},
            alertmanager_headers={"Authorization": f"Bearer {alertmanager_token}"},
            cloudevents_headers={"Authorization": f"Bearer {cloudevents_token}"},
        )


def service_payload(name: str, environment: str = "production") -> dict[str, str]:
    return {
        "service": name,
        "environment": environment,
        "owner_team": "platform",
    }


@pytest.mark.parametrize("credential", ["none", "alertmanager", "cloudevents"])
def test_only_manual_token_can_create_catalog_service(
    context: CatalogApiContext,
    credential: str,
) -> None:
    headers = {
        "none": {},
        "alertmanager": context.alertmanager_headers,
        "cloudevents": context.cloudevents_headers,
    }[credential]

    response = context.client.post(
        "/api/v1/catalog/services",
        headers=headers,
        json=service_payload("payment-api"),
    )

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


def test_service_crud_list_and_stale_version_contract(context: CatalogApiContext) -> None:
    created = context.client.post(
        "/api/v1/catalog/services",
        headers=context.manual_headers,
        json=service_payload("payment-api"),
    )
    assert created.status_code == 201
    service = created.json()
    assert set(service) == {
        "id",
        "service",
        "environment",
        "owner_team",
        "state",
        "created_at",
        "updated_at",
        "version",
    }
    assert service["version"] == 1

    fetched = context.client.get(
        f"/api/v1/catalog/services/{service['id']}",
        headers=context.manual_headers,
    )
    listed = context.client.get(
        "/api/v1/catalog/services?state=ACTIVE&environment=production&limit=50&offset=0",
        headers=context.manual_headers,
    )
    assert fetched.status_code == 200
    assert fetched.json() == service
    assert listed.status_code == 200
    assert listed.json()["items"] == [service]
    assert listed.json()["limit"] == 50
    assert listed.json()["offset"] == 0

    updated = context.client.patch(
        f"/api/v1/catalog/services/{service['id']}",
        headers=context.manual_headers,
        json={"expected_version": 1, "state": "INACTIVE"},
    )
    assert updated.status_code == 200
    assert updated.json()["state"] == "INACTIVE"
    assert updated.json()["version"] == 2

    stale = context.client.patch(
        f"/api/v1/catalog/services/{service['id']}",
        headers=context.manual_headers,
        json={"expected_version": 1, "owner_team": "payments"},
    )
    assert stale.status_code == 409
    assert stale.json() == {
        "code": "catalog_version_conflict",
        "message": "资源版本已变化，请刷新后重试",
    }


def test_dependency_create_list_update_and_invalid_contract(
    context: CatalogApiContext,
) -> None:
    service_a = context.client.post(
        "/api/v1/catalog/services",
        headers=context.manual_headers,
        json=service_payload("service-a"),
    ).json()
    service_b = context.client.post(
        "/api/v1/catalog/services",
        headers=context.manual_headers,
        json=service_payload("service-b"),
    ).json()
    created = context.client.post(
        "/api/v1/catalog/dependencies",
        headers=context.manual_headers,
        json={
            "caller_service_id": service_a["id"],
            "dependency_service_id": service_b["id"],
        },
    )
    assert created.status_code == 201
    dependency = created.json()
    assert dependency["state"] == "ACTIVE"
    assert dependency["version"] == 1

    listed = context.client.get(
        f"/api/v1/catalog/dependencies?service_id={service_a['id']}&state=ACTIVE",
        headers=context.manual_headers,
    )
    assert listed.status_code == 200
    assert listed.json()["items"] == [dependency]

    updated = context.client.patch(
        f"/api/v1/catalog/dependencies/{dependency['id']}",
        headers=context.manual_headers,
        json={"expected_version": 1, "state": "INACTIVE"},
    )
    assert updated.status_code == 200
    assert updated.json()["state"] == "INACTIVE"
    assert updated.json()["version"] == 2

    invalid = context.client.post(
        "/api/v1/catalog/dependencies",
        headers=context.manual_headers,
        json={
            "caller_service_id": service_a["id"],
            "dependency_service_id": service_a["id"],
        },
    )
    assert invalid.status_code == 422
    assert invalid.json() == {
        "code": "invalid_dependency",
        "message": "服务依赖关系不符合约束",
    }


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("get", "/api/v1/catalog/services?limit=101", None),
        ("get", "/api/v1/catalog/services?offset=10001", None),
        (
            "post",
            "/api/v1/catalog/services",
            {
                "service": "payment-api",
                "environment": "production",
                "owner_team": "platform",
                "scenario_id": "forbidden",
            },
        ),
    ],
)
def test_catalog_api_rejects_unbounded_or_forbidden_input(
    context: CatalogApiContext,
    method: str,
    path: str,
    json_body: dict[str, str] | None,
) -> None:
    response = context.client.request(
        method,
        path,
        headers=context.manual_headers,
        json=json_body,
    )
    assert response.status_code == 422
    assert response.json()["code"] in {"validation_error", "forbidden_identity"}
