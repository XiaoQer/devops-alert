from __future__ import annotations

from collections.abc import Callable, Iterator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from incident_intelligence.main import create_app
from incident_intelligence.persistence.models import (
    AlertRow,
    AuditEventRow,
    DiagnosisRunRow,
    IncidentRow,
    SignalEventRow,
)
from incident_intelligence.settings import Settings

CLOUD_PATH = "/api/v1/intake/cloudevents"


@pytest.fixture
def cloud_client(
    migrated_engine: Engine,
    settings_factory: Callable[..., Settings],
) -> Iterator[tuple[TestClient, dict[str, str]]]:
    tokens = {
        "manual": token_urlsafe(32),
        "alertmanager": token_urlsafe(32),
        "cloudevents": token_urlsafe(32),
    }
    settings = settings_factory(
        api_token=SecretStr(tokens["manual"]),
        alertmanager_token=SecretStr(tokens["alertmanager"]),
        cloudevents_token=SecretStr(tokens["cloudevents"]),
    )
    app = create_app(settings, engine=migrated_engine)
    with TestClient(app) as client:
        yield client, tokens


@pytest.fixture
def event_parts() -> dict[str, object]:
    now = datetime.now(UTC)
    data = {
        "alert_key": "payment-error-rate",
        "title": "支付接口错误率升高",
        "summary": "支付接口错误率超过阈值",
        "severity": "high",
        "service": "payment-api",
        "environment": "production",
        "status": "firing",
        "started_at": (now - timedelta(minutes=2)).isoformat(),
        "labels": {"region": "cn-east-1"},
    }
    context = {
        "specversion": "1.0",
        "id": "cloud-event-1",
        "source": "https://events.example.com/monitor-a?token=must-not-persist",
        "type": "com.incidentintelligence.alert.v1",
        "subject": "payment-api",
        "time": (now - timedelta(minutes=1)).isoformat(),
        "datacontenttype": "application/json",
    }
    return {"context": context, "data": data}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _structured(
    client: TestClient,
    tokens: dict[str, str],
    parts: dict[str, object],
) -> object:
    return client.post(
        CLOUD_PATH,
        headers={
            **_auth(tokens["cloudevents"]),
            "Content-Type": "application/cloudevents+json",
        },
        json={**parts["context"], "data": parts["data"]},  # type: ignore[misc]
    )


def _binary_headers(token: str, context: dict[str, object]) -> dict[str, str]:
    headers = {
        **_auth(token),
        "Content-Type": "application/json",
        "ce-specversion": str(context["specversion"]),
        "ce-id": str(context["id"]),
        "ce-source": str(context["source"]),
        "ce-type": str(context["type"]),
        "ce-time": str(context["time"]),
    }
    if context.get("subject") is not None:
        headers["ce-subject"] = str(context["subject"])
    return headers


def _binary(
    client: TestClient,
    tokens: dict[str, str],
    parts: dict[str, object],
) -> object:
    return client.post(
        CLOUD_PATH,
        headers=_binary_headers(tokens["cloudevents"], parts["context"]),  # type: ignore[arg-type]
        json=parts["data"],
    )


def _count(engine: Engine, row_type: type[object]) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(row_type)) or 0


def test_structured_and_binary_events_update_one_alert(
    cloud_client: tuple[TestClient, dict[str, str]],
    event_parts: dict[str, object],
    migrated_engine: Engine,
) -> None:
    client, tokens = cloud_client
    first = _structured(client, tokens, event_parts)
    update = deepcopy(event_parts)
    update["context"]["id"] = "cloud-event-2"  # type: ignore[index]
    update["context"]["time"] = datetime.now(UTC).isoformat()  # type: ignore[index]
    update["data"]["summary"] = "错误率仍在升高"  # type: ignore[index]
    second = _binary(client, tokens, update)

    assert first.status_code == 202  # type: ignore[attr-defined]
    assert second.status_code == 202  # type: ignore[attr-defined]
    assert first.json()["items"][0]["alert_id"] == second.json()["items"][0]["alert_id"]  # type: ignore[attr-defined]
    assert first.json()["counts"]["opened"] == 1  # type: ignore[attr-defined]
    assert second.json()["counts"]["updated"] == 1  # type: ignore[attr-defined]
    assert _count(migrated_engine, SignalEventRow) == 2
    assert _count(migrated_engine, AlertRow) == 1
    assert _count(migrated_engine, IncidentRow) == 0
    assert _count(migrated_engine, DiagnosisRunRow) == 0


@pytest.mark.parametrize("token_name", [None, "manual", "alertmanager"])
def test_only_cloudevents_token_can_access_the_endpoint(
    cloud_client: tuple[TestClient, dict[str, str]],
    event_parts: dict[str, object],
    token_name: str | None,
) -> None:
    client, tokens = cloud_client
    headers = {
        "Content-Type": "application/cloudevents+json",
        **({} if token_name is None else _auth(tokens[token_name])),
    }

    response = client.post(
        CLOUD_PATH,
        headers=headers,
        json={**event_parts["context"], "data": event_parts["data"]},  # type: ignore[misc]
    )

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


def test_cloudevents_token_cannot_access_other_intake_endpoints(
    cloud_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, tokens = cloud_client

    manual = client.post(
        "/api/v1/manual-reports",
        headers={**_auth(tokens["cloudevents"]), "Idempotency-Key": "token-isolation"},
        json={},
    )
    alertmanager = client.post(
        "/api/v1/intake/alertmanager",
        headers=_auth(tokens["cloudevents"]),
        json={},
    )

    assert manual.status_code == 401
    assert alertmanager.status_code == 401


def test_content_type_dispatch_is_strict_and_safe(
    cloud_client: tuple[TestClient, dict[str, str]],
    event_parts: dict[str, object],
) -> None:
    client, tokens = cloud_client

    response = client.post(
        CLOUD_PATH,
        headers={**_auth(tokens["cloudevents"]), "Content-Type": "text/plain"},
        content=b"private-body",
    )

    assert response.status_code == 415
    assert response.json() == {
        "code": "unsupported_media_type",
        "message": "不支持该事件内容类型",
    }
    assert "private-body" not in response.text


def test_exact_replay_returns_200_and_changed_identity_content_returns_conflict(
    cloud_client: tuple[TestClient, dict[str, str]],
    event_parts: dict[str, object],
    migrated_engine: Engine,
) -> None:
    client, tokens = cloud_client
    first = _structured(client, tokens, event_parts)
    replay = _binary(client, tokens, event_parts)
    conflicting = deepcopy(event_parts)
    conflicting["data"]["summary"] = "相同事件身份下的不同内容"  # type: ignore[index]
    conflict = _structured(client, tokens, conflicting)

    assert first.status_code == 202  # type: ignore[attr-defined]
    assert replay.status_code == 200  # type: ignore[attr-defined]
    assert replay.json()["items"][0]["replayed"] is True  # type: ignore[attr-defined]
    assert conflict.status_code == 409  # type: ignore[attr-defined]
    assert conflict.json()["code"] == "source_event_conflict"  # type: ignore[attr-defined]
    assert _count(migrated_engine, SignalEventRow) == 1
    assert _count(migrated_engine, AuditEventRow) == 2


def test_resolved_then_older_firing_does_not_reopen_alert(
    cloud_client: tuple[TestClient, dict[str, str]],
    event_parts: dict[str, object],
    migrated_engine: Engine,
) -> None:
    client, tokens = cloud_client
    _structured(client, tokens, event_parts)
    resolved = deepcopy(event_parts)
    resolved["context"]["id"] = "cloud-event-resolved"  # type: ignore[index]
    resolved["context"]["time"] = datetime.now(UTC).isoformat()  # type: ignore[index]
    resolved["data"]["status"] = "resolved"  # type: ignore[index]
    resolved_response = _binary(client, tokens, resolved)
    stale = deepcopy(event_parts)
    stale["context"]["id"] = "cloud-event-stale"  # type: ignore[index]
    stale_response = _structured(client, tokens, stale)

    assert resolved_response.json()["counts"]["resolved"] == 1  # type: ignore[attr-defined]
    assert stale_response.json()["counts"]["stale"] == 1  # type: ignore[attr-defined]
    with Session(migrated_engine) as session:
        assert session.scalar(select(AlertRow.state)) == "RESOLVED"


def test_body_over_64_kib_is_rejected_before_json_parsing(
    cloud_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, tokens = cloud_client

    response = client.post(
        CLOUD_PATH,
        headers={
            **_auth(tokens["cloudevents"]),
            "Content-Type": "application/cloudevents+json",
        },
        content=b"x" * 65_537,
    )

    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"


def test_unsupported_event_type_and_missing_binary_headers_use_safe_errors(
    cloud_client: tuple[TestClient, dict[str, str]],
    event_parts: dict[str, object],
) -> None:
    client, tokens = cloud_client
    unsupported = deepcopy(event_parts)
    unsupported["context"]["type"] = "com.example.private.v1"  # type: ignore[index]

    unsupported_response = _structured(client, tokens, unsupported)
    binary_context = deepcopy(event_parts["context"])
    binary_context["type"] = "com.example.private.v1"  # type: ignore[index]
    unsupported_binary = client.post(
        CLOUD_PATH,
        headers=_binary_headers(tokens["cloudevents"], binary_context),  # type: ignore[arg-type]
        json=event_parts["data"],
    )
    missing_headers = client.post(
        CLOUD_PATH,
        headers={**_auth(tokens["cloudevents"]), "Content-Type": "application/json"},
        json=event_parts["data"],
    )

    assert unsupported_response.status_code == 422  # type: ignore[attr-defined]
    assert unsupported_response.json()["code"] == "unsupported_event_type"  # type: ignore[attr-defined]
    assert unsupported_binary.status_code == 422
    assert unsupported_binary.json()["code"] == "unsupported_event_type"
    assert missing_headers.status_code == 422
    assert missing_headers.json()["code"] == "validation_error"


def test_forbidden_identity_is_rejected_without_persistence_or_echo(
    cloud_client: tuple[TestClient, dict[str, str]],
    event_parts: dict[str, object],
    migrated_engine: Engine,
) -> None:
    client, tokens = cloud_client
    forbidden = deepcopy(event_parts)
    forbidden["data"]["labels"]["scenario_id"] = "must-not-be-echoed"  # type: ignore[index]

    response = _structured(client, tokens, forbidden)

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert response.json()["code"] == "forbidden_identity"  # type: ignore[attr-defined]
    assert "must-not-be-echoed" not in response.text  # type: ignore[attr-defined]
    assert _count(migrated_engine, SignalEventRow) == 0


def test_persisted_records_do_not_contain_raw_source_or_unreviewed_fields(
    cloud_client: tuple[TestClient, dict[str, str]],
    event_parts: dict[str, object],
    migrated_engine: Engine,
) -> None:
    client, tokens = cloud_client
    response = _structured(client, tokens, event_parts)

    assert response.status_code == 202  # type: ignore[attr-defined]
    assert "events.example.com" not in response.text  # type: ignore[attr-defined]
    assert "must-not-persist" not in response.text  # type: ignore[attr-defined]
    with Session(migrated_engine) as session:
        signal = session.scalar(select(SignalEventRow))
        audits = tuple(session.scalars(select(AuditEventRow)))
        assert signal is not None
        assert signal.facts == {"region": "cn-east-1"}
        assert all(
            set(audit.details) <= {"reason_code", "adapter", "parent_id"} for audit in audits
        )
        serialized = " ".join(str(value) for value in (signal.facts, *(a.details for a in audits)))
        assert "events.example.com" not in serialized
        assert "must-not-persist" not in serialized
