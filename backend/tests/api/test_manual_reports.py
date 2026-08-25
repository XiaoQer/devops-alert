from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from incident_intelligence.main import create_app
from incident_intelligence.settings import Settings


@pytest.fixture
def api_client(migrated_engine: Engine) -> Iterator[tuple[TestClient, dict[str, str]]]:
    token = token_urlsafe(32)
    settings = Settings(
        database_url="mysql+pymysql://test-client@127.0.0.1/unused",
        api_token=SecretStr(token),
    )
    app = create_app(settings, engine=migrated_engine)
    with TestClient(app) as client:
        yield client, {"Authorization": f"Bearer {token}"}


@pytest.fixture
def valid_report() -> dict[str, object]:
    return {
        "title": "支付接口错误率升高",
        "summary": "支付接口在生产环境持续返回错误",
        "severity": "high",
        "service": "payment-api",
        "environment": "production",
        "observed_at": datetime.now(UTC).isoformat(),
        "labels": {"region": "cn-east-1", "symptom": "high-error-rate"},
    }


def submit(
    client: TestClient,
    auth_headers: dict[str, str],
    report: dict[str, object],
    *,
    key: str = "key-1",
) -> object:
    return client.post(
        "/api/v1/manual-reports",
        headers={**auth_headers, "Idempotency-Key": key},
        json=report,
    )


def test_manual_report_requires_bearer_token(
    api_client: tuple[TestClient, dict[str, str]], valid_report: dict[str, object]
) -> None:
    client, _ = api_client

    response = client.post("/api/v1/manual-reports", json=valid_report)

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


def test_valid_report_creates_and_replays_the_same_records(
    api_client: tuple[TestClient, dict[str, str]], valid_report: dict[str, object]
) -> None:
    client, auth_headers = api_client

    first = submit(client, auth_headers, valid_report)
    second = submit(client, auth_headers, valid_report)

    assert first.status_code == 201  # type: ignore[attr-defined]
    assert second.status_code == 200  # type: ignore[attr-defined]
    assert second.json()["replayed"] is True  # type: ignore[attr-defined]
    assert {
        key: value
        for key, value in second.json().items()
        if key != "replayed"  # type: ignore[attr-defined]
    } == {key: value for key, value in first.json().items() if key != "replayed"}  # type: ignore[attr-defined]


def test_changed_payload_under_same_key_returns_conflict(
    api_client: tuple[TestClient, dict[str, str]], valid_report: dict[str, object]
) -> None:
    client, auth_headers = api_client
    submit(client, auth_headers, valid_report)
    changed = {**valid_report, "summary": "另一份事故描述"}

    response = submit(client, auth_headers, changed)

    assert response.status_code == 409  # type: ignore[attr-defined]
    assert response.json()["code"] == "idempotency_conflict"  # type: ignore[attr-defined]


@pytest.mark.parametrize("authorization", [None, "Basic abc", "Bearer wrong-token"])
def test_missing_or_invalid_token_returns_same_safe_error(
    api_client: tuple[TestClient, dict[str, str]],
    valid_report: dict[str, object],
    authorization: str | None,
) -> None:
    client, _ = api_client
    headers = {"Idempotency-Key": "key-1"}
    if authorization is not None:
        headers["Authorization"] = authorization

    response = client.post("/api/v1/manual-reports", headers=headers, json=valid_report)

    assert response.status_code == 401
    assert response.json() == {
        "code": "authentication_required",
        "message": "需要有效的访问凭证",
    }


@pytest.mark.parametrize("key", [None, "", "x" * 257])
def test_missing_or_malformed_idempotency_key_returns_400(
    api_client: tuple[TestClient, dict[str, str]],
    valid_report: dict[str, object],
    key: str | None,
) -> None:
    client, auth_headers = api_client
    headers = dict(auth_headers)
    if key is not None:
        headers["Idempotency-Key"] = key

    response = client.post("/api/v1/manual-reports", headers=headers, json=valid_report)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_idempotency_key"


@pytest.mark.parametrize(
    "forbidden_key",
    ["scenario_id", "Scenario-ID", "scenario version", "experiment_id", "ground_truth", "注入动作"],
)
def test_forbidden_identity_is_rejected_at_nested_label_depth(
    api_client: tuple[TestClient, dict[str, str]],
    valid_report: dict[str, object],
    forbidden_key: str,
) -> None:
    client, auth_headers = api_client
    report = {**valid_report, "labels": {forbidden_key: "hidden"}}

    response = submit(client, auth_headers, report)

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert response.json() == {  # type: ignore[attr-defined]
        "code": "forbidden_identity",
        "message": "请求包含平台禁止接收的字段",
    }


def test_unknown_non_experiment_field_uses_safe_validation_error(
    api_client: tuple[TestClient, dict[str, str]], valid_report: dict[str, object]
) -> None:
    client, auth_headers = api_client
    report = {**valid_report, "unexpected": "raw-value-must-not-be-echoed"}

    response = submit(client, auth_headers, report)

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert response.json() == {  # type: ignore[attr-defined]
        "code": "validation_error",
        "message": "请求字段不符合约束",
    }
    assert "raw-value-must-not-be-echoed" not in response.text  # type: ignore[attr-defined]


def test_request_body_over_limit_is_rejected_before_parsing(
    api_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, auth_headers = api_client
    oversized = b'{"padding":"' + (b"x" * 65_536) + b'"}'

    response = client.post(
        "/api/v1/manual-reports",
        headers={**auth_headers, "Idempotency-Key": "key-1", "Content-Type": "application/json"},
        content=oversized,
    )

    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"


def test_streamed_request_body_over_limit_is_also_rejected(
    api_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, auth_headers = api_client

    def body_chunks() -> Iterator[bytes]:
        yield b'{"padding":"'
        yield b"x" * 40_000
        yield b"x" * 30_000
        yield b'"}'

    response = client.post(
        "/api/v1/manual-reports",
        headers={**auth_headers, "Idempotency-Key": "key-1", "Content-Type": "application/json"},
        content=body_chunks(),
    )

    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"


def test_future_time_and_label_count_are_bounded(
    api_client: tuple[TestClient, dict[str, str]], valid_report: dict[str, object]
) -> None:
    client, auth_headers = api_client
    future = {
        **valid_report,
        "observed_at": (datetime.now(UTC) + timedelta(minutes=6)).isoformat(),
    }
    too_many_labels = {**valid_report, "labels": {f"key-{index}": "value" for index in range(21)}}

    future_response = submit(client, auth_headers, future, key="future")
    labels_response = submit(client, auth_headers, too_many_labels, key="labels")

    assert future_response.status_code == 422  # type: ignore[attr-defined]
    assert future_response.json()["code"] == "validation_error"  # type: ignore[attr-defined]
    assert labels_response.status_code == 422  # type: ignore[attr-defined]
    assert labels_response.json()["code"] == "validation_error"  # type: ignore[attr-defined]


def test_database_failure_is_safe_and_does_not_log_secrets_or_body(
    api_client: tuple[TestClient, dict[str, str]],
    valid_report: dict[str, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, auth_headers = api_client

    class FailingService:
        def submit(self, *args: object, **kwargs: object) -> None:
            raise OperationalError("statement", {}, OSError("database unavailable"))

    client.app.state.manual_intake_service = FailingService()
    response = submit(client, auth_headers, valid_report)

    assert response.status_code == 503  # type: ignore[attr-defined]
    assert response.json() == {  # type: ignore[attr-defined]
        "code": "persistence_unavailable",
        "message": "事故记录暂时无法保存。请稍后重试",
    }
    captured = caplog.text
    assert auth_headers["Authorization"] not in captured
    assert str(valid_report["summary"]) not in captured
