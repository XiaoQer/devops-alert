from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

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
