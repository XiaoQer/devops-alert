from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from secrets import token_urlsafe

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from pydantic import SecretStr
from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine

from incident_intelligence.main import create_app
from incident_intelligence.settings import Settings


@dataclass(frozen=True)
class CreatedResources:
    client: TestClient
    auth_headers: dict[str, str]
    ids: dict[str, str]
    engine: Engine


@pytest.fixture
def created_resources(migrated_engine: Engine) -> Iterator[CreatedResources]:
    token = token_urlsafe(32)
    settings = Settings(
        database_url="mysql+pymysql://test-client@127.0.0.1/unused",
        api_token=SecretStr(token),
        alertmanager_token=SecretStr(token_urlsafe(32)),
        cloudevents_token=SecretStr(token_urlsafe(32)),
    )
    app = create_app(settings, engine=migrated_engine)
    auth_headers = {"Authorization": f"Bearer {token}"}
    report = {
        "title": "支付接口错误率升高",
        "summary": "支付接口在生产环境持续返回错误",
        "severity": "high",
        "service": "payment-api",
        "environment": "production",
        "observed_at": datetime.now(UTC).isoformat(),
        "labels": {"region": "cn-east-1", "symptom": "high-error-rate"},
    }
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/manual-reports",
            headers={**auth_headers, "Idempotency-Key": "resource-read-key"},
            json=report,
        )
        assert response.status_code == 201
        yield CreatedResources(client, auth_headers, response.json(), migrated_engine)


RESOURCE_PATHS = {
    "signal_event_id": "/api/v1/signals/{}",
    "alert_id": "/api/v1/alerts/{}",
    "incident_id": "/api/v1/incidents/{}",
    "diagnosis_run_id": "/api/v1/diagnosis-runs/{}",
}


def get_resource(context: CreatedResources, id_field: str) -> Response:
    return context.client.get(
        RESOURCE_PATHS[id_field].format(context.ids[id_field]),
        headers=context.auth_headers,
    )


def test_created_resources_can_be_read_independently(created_resources: CreatedResources) -> None:
    for id_field in RESOURCE_PATHS:
        response = get_resource(created_resources, id_field)

        assert response.status_code == 200
        assert response.json()["id"] == created_resources.ids[id_field]


def test_each_resource_exposes_only_its_normalized_contract(
    created_resources: CreatedResources,
) -> None:
    expected_fields = {
        "signal_event_id": {
            "id",
            "source",
            "event_type",
            "title",
            "summary",
            "severity",
            "service",
            "environment",
            "observed_at",
            "received_at",
            "facts",
            "created_at",
            "version",
        },
        "alert_id": {
            "id",
            "signal_event_id",
            "source",
            "source_instance",
            "source_alert_key",
            "state",
            "title",
            "severity",
            "service",
            "environment",
            "first_observed_at",
            "last_observed_at",
            "state_changed_at",
            "created_at",
            "version",
        },
        "incident_id": {
            "id",
            "primary_alert_id",
            "state",
            "title",
            "severity",
            "service",
            "environment",
            "detected_at",
            "created_at",
            "version",
        },
        "diagnosis_run_id": {
            "id",
            "incident_id",
            "incident_context_version",
            "state",
            "created_at",
            "version",
        },
    }

    for id_field, fields in expected_fields.items():
        response = get_resource(created_resources, id_field)

        assert response.status_code == 200
        assert set(response.json()) == fields


@pytest.mark.parametrize("id_field", list(RESOURCE_PATHS))
def test_all_resource_reads_require_authentication(
    created_resources: CreatedResources, id_field: str
) -> None:
    response = created_resources.client.get(
        RESOURCE_PATHS[id_field].format(created_resources.ids[id_field])
    )

    assert response.status_code == 401
    assert response.json() == {
        "code": "authentication_required",
        "message": "需要有效的访问凭证",
    }


@pytest.mark.parametrize(
    ("path", "missing_id"),
    [
        ("/api/v1/signals/{}", "sig_00000000000000000000000000000000"),
        ("/api/v1/alerts/{}", "alt_00000000000000000000000000000000"),
        ("/api/v1/incidents/{}", "inc_00000000000000000000000000000000"),
        ("/api/v1/diagnosis-runs/{}", "diag_00000000000000000000000000000000"),
    ],
)
def test_absent_resource_returns_stable_not_found(
    created_resources: CreatedResources, path: str, missing_id: str
) -> None:
    response = created_resources.client.get(
        path.format(missing_id), headers=created_resources.auth_headers
    )

    assert response.status_code == 404
    assert response.json() == {
        "code": "resource_not_found",
        "message": "未找到指定资源",
    }


@pytest.mark.parametrize("path", list(RESOURCE_PATHS.values()))
def test_malformed_resource_id_returns_the_same_not_found_contract(
    created_resources: CreatedResources, path: str
) -> None:
    response = created_resources.client.get(
        path.format("wrong-prefix"), headers=created_resources.auth_headers
    )

    assert response.status_code == 404
    assert response.json() == {
        "code": "resource_not_found",
        "message": "未找到指定资源",
    }


@pytest.mark.parametrize(
    ("id_field", "expected_table"),
    [
        ("signal_event_id", "signal_events"),
        ("alert_id", "alerts"),
        ("incident_id", "incidents"),
        ("diagnosis_run_id", "diagnosis_runs"),
    ],
)
def test_each_resource_read_uses_one_focused_query(
    created_resources: CreatedResources, id_field: str, expected_table: str
) -> None:
    statements: list[str] = []

    def capture_statement(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, context, executemany
        statements.append(statement.casefold())

    event.listen(created_resources.engine, "before_cursor_execute", capture_statement)
    try:
        response = get_resource(created_resources, id_field)
    finally:
        event.remove(created_resources.engine, "before_cursor_execute", capture_statement)

    assert response.status_code == 200
    assert len(statements) == 1
    assert expected_table in statements[0]
    assert all(
        table == expected_table or table not in statements[0]
        for table in ("signal_events", "alerts", "incidents", "diagnosis_runs")
    )
