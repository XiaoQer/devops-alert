from __future__ import annotations

import json
from collections.abc import Callable

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.engine import Engine

from incident_intelligence.main import create_app
from incident_intelligence.settings import Settings


class AvailableConnection:
    def execute(self, statement: object) -> None:
        return None

    def __enter__(self) -> AvailableConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class AvailableEngine:
    def connect(self) -> AvailableConnection:
        return AvailableConnection()


class UnavailableEngine:
    def connect(self) -> None:
        raise OSError("database unavailable")


def test_liveness_does_not_require_database(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_reports_available_database(
    settings_factory: Callable[..., Settings],
) -> None:
    app = create_app(settings_factory(), engine=AvailableEngine())  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "available"}


def test_readiness_reports_unavailable_database(
    settings_factory: Callable[..., Settings],
) -> None:
    app = create_app(settings_factory(), engine=UnavailableEngine())  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "database": "unavailable"}


def test_health_exposes_bounded_worker_status_without_secrets(
    migrated_engine: Engine,
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory(
        database_url=str(migrated_engine.url),
        incident_workers_enabled=False,
        feishu_app_id=SecretStr("app-id-not-public"),
        feishu_app_secret=SecretStr("secret-not-public"),
        feishu_verification_token=SecretStr("token-not-public"),
    )
    app = create_app(settings, engine=migrated_engine)
    with TestClient(app) as test_client:
        response = test_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["database"] == "available"
    assert body["incident_evaluation"] == {
        "pending_count": 0,
        "leased_count": 0,
        "failed_count": 0,
        "oldest_pending_at": None,
        "last_success_at": None,
        "last_error_code": None,
    }
    assert body["incident_notification"] == body["incident_evaluation"]
    assert body["evidence_collection"] == body["incident_evaluation"]
    assert body["monitoring_sources"] == {
        "PROMETHEUS": {"configured": False, "last_connection_state": None},
        "ELASTICSEARCH": {"configured": False, "last_connection_state": None},
        "SKYWALKING": {"configured": False, "last_connection_state": None},
    }
    assert body["feishu"] == {"configured": True}
    rendered = json.dumps(body)
    assert settings.api_token.get_secret_value() not in rendered
    assert settings.feishu_app_secret.get_secret_value() not in rendered
    for forbidden in ("payload", "task_id", "traceback", "chat_id"):
        assert forbidden not in rendered
