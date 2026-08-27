from __future__ import annotations

from collections.abc import Callable, Iterator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from incident_intelligence.main import create_app
from incident_intelligence.persistence.models import (
    AlertRow,
    AuditEventRow,
    DiagnosisRunRow,
    IncidentRow,
    SignalEventRow,
)
from incident_intelligence.services.signal_intake import SourceEventConflict
from incident_intelligence.settings import Settings


@pytest.fixture
def intake_client(
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
def firing_payload() -> dict[str, object]:
    starts_at = datetime.now(UTC) - timedelta(minutes=1)
    return {
        "version": "4",
        "groupKey": '{}:{alertname="PaymentHighErrorRate"}',
        "truncatedAlerts": 0,
        "status": "firing",
        "receiver": "incident-intelligence",
        "groupLabels": {"alertname": "PaymentHighErrorRate"},
        "commonLabels": {"service": "payment-api"},
        "commonAnnotations": {},
        "externalURL": "https://alertmanager.example.com:9093/cluster-a?token=secret",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "PaymentHighErrorRate",
                    "service": "payment-api",
                    "environment": "production",
                    "severity": "high",
                    "region": "cn-east-1",
                },
                "annotations": {
                    "summary": "支付接口错误率升高",
                    "description": "支付接口错误率超过阈值",
                },
                "startsAt": starts_at.isoformat(),
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "https://prometheus.example.com/graph?g0.expr=private_query",
                "fingerprint": "fingerprint-1",
            }
        ],
    }


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _post(
    client: TestClient,
    tokens: dict[str, str],
    payload: dict[str, object],
) -> object:
    return client.post(
        "/api/v1/intake/alertmanager",
        headers=_headers(tokens["alertmanager"]),
        json=payload,
    )


def _row_count(engine: Engine, row_type: type[object]) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(row_type)) or 0


def test_valid_batch_is_accepted_without_creating_incident_or_diagnosis(
    intake_client: tuple[TestClient, dict[str, str]],
    firing_payload: dict[str, object],
    migrated_engine: Engine,
) -> None:
    client, tokens = intake_client

    response = _post(client, tokens, firing_payload)

    assert response.status_code == 202  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["counts"] == {
        "opened": 1,
        "updated": 0,
        "resolved": 0,
        "reopened": 0,
        "stale": 0,
        "orphan_resolved": 0,
        "replayed": 0,
    }
    assert len(body["items"]) == 1
    assert body["items"][0]["outcome"] == "opened"
    assert body["items"][0]["replayed"] is False
    assert _row_count(migrated_engine, SignalEventRow) == 1
    assert _row_count(migrated_engine, AlertRow) == 1
    assert _row_count(migrated_engine, IncidentRow) == 0
    assert _row_count(migrated_engine, DiagnosisRunRow) == 0


def test_alert_without_service_is_persisted_with_real_pod_identity(
    intake_client: tuple[TestClient, dict[str, str]],
    firing_payload: dict[str, object],
    migrated_engine: Engine,
) -> None:
    client, tokens = intake_client
    firing_payload["commonLabels"] = {}
    labels = firing_payload["alerts"][0]["labels"]  # type: ignore[index]
    del labels["service"]  # type: ignore[index]
    labels.update(  # type: ignore[union-attr]
        {"namespace": "devops-platform", "pod": "aegis-springboot-demo-0"}
    )

    response = _post(client, tokens, firing_payload)

    assert response.status_code == 202  # type: ignore[attr-defined]
    with Session(migrated_engine) as session:
        alert = session.scalar(select(AlertRow))
        signal = session.scalar(select(SignalEventRow))
        assert alert is not None
        assert signal is not None
        assert alert.service is None
        assert signal.service is None
        assert alert.entity_type == "POD"
        assert alert.entity_display_name == "devops-platform/aegis-springboot-demo-0"
        assert signal.entity_key == alert.entity_key
        session.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table_name in (
            "alert_grouping_jobs",
            "signal_intake_results",
            "audit_events",
            "alerts",
            "signal_events",
        ):
            session.execute(text(f"DELETE FROM {table_name}"))
        session.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        session.commit()


@pytest.mark.parametrize("token_name", [None, "manual", "cloudevents"])
def test_only_alertmanager_token_can_access_the_endpoint(
    intake_client: tuple[TestClient, dict[str, str]],
    firing_payload: dict[str, object],
    token_name: str | None,
) -> None:
    client, tokens = intake_client
    headers = {} if token_name is None else _headers(tokens[token_name])

    response = client.post(
        "/api/v1/intake/alertmanager",
        headers=headers,
        json=firing_payload,
    )

    assert response.status_code == 401
    assert response.json() == {
        "code": "authentication_required",
        "message": "需要有效的访问凭证",
    }


def test_alertmanager_token_cannot_access_manual_intake(
    intake_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, tokens = intake_client

    response = client.post(
        "/api/v1/manual-reports",
        headers={
            **_headers(tokens["alertmanager"]),
            "Idempotency-Key": "isolated-token-check",
        },
        json={},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


def test_alertmanager_has_its_own_256_kib_body_limit(
    intake_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, tokens = intake_client
    below_limit_but_invalid = b'{"padding":"' + (b"x" * 70_000) + b'"}'

    below = client.post(
        "/api/v1/intake/alertmanager",
        headers={**_headers(tokens["alertmanager"]), "Content-Type": "application/json"},
        content=below_limit_but_invalid,
    )
    above = client.post(
        "/api/v1/intake/alertmanager",
        headers={**_headers(tokens["alertmanager"]), "Content-Type": "application/json"},
        content=b"x" * 262_145,
    )

    assert below.status_code == 422
    assert below.json()["code"] == "validation_error"
    assert above.status_code == 413
    assert above.json()["code"] == "request_too_large"


def test_batch_over_100_alerts_uses_specific_safe_error(
    intake_client: tuple[TestClient, dict[str, str]],
    firing_payload: dict[str, object],
) -> None:
    client, tokens = intake_client
    payload = deepcopy(firing_payload)
    payload["alerts"] = [deepcopy(firing_payload["alerts"][0]) for _ in range(101)]  # type: ignore[index]

    response = _post(client, tokens, payload)

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert response.json() == {  # type: ignore[attr-defined]
        "code": "batch_too_large",
        "message": "Alertmanager 单批最多接收 100 条告警",
    }


def test_multi_alert_batch_preserves_order_and_exact_replay_returns_200(
    intake_client: tuple[TestClient, dict[str, str]],
    firing_payload: dict[str, object],
    migrated_engine: Engine,
) -> None:
    client, tokens = intake_client
    payload = deepcopy(firing_payload)
    second = deepcopy(firing_payload["alerts"][0])  # type: ignore[index]
    second["fingerprint"] = "fingerprint-2"
    second["labels"]["alertname"] = "PaymentHighLatency"  # type: ignore[index]
    second["annotations"]["summary"] = "支付接口延迟升高"  # type: ignore[index]
    payload["alerts"] = [payload["alerts"][0], second]  # type: ignore[index]

    first = _post(client, tokens, payload)
    replay = _post(client, tokens, payload)

    assert first.status_code == 202  # type: ignore[attr-defined]
    assert replay.status_code == 200  # type: ignore[attr-defined]
    assert [item["replayed"] for item in replay.json()["items"]] == [True, True]  # type: ignore[attr-defined]
    assert replay.json()["counts"]["replayed"] == 2  # type: ignore[attr-defined]
    assert [item["alert_id"] for item in replay.json()["items"]] == [  # type: ignore[attr-defined]
        item["alert_id"]
        for item in first.json()["items"]  # type: ignore[attr-defined]
    ]
    assert _row_count(migrated_engine, SignalEventRow) == 2
    assert _row_count(migrated_engine, AuditEventRow) == 4


def test_content_update_and_resolution_project_the_existing_alert(
    intake_client: tuple[TestClient, dict[str, str]],
    firing_payload: dict[str, object],
    migrated_engine: Engine,
) -> None:
    client, tokens = intake_client
    first = _post(client, tokens, firing_payload)
    updated = deepcopy(firing_payload)
    updated["alerts"][0]["annotations"]["description"] = "错误率继续升高"  # type: ignore[index]
    update_response = _post(client, tokens, updated)
    resolved = deepcopy(updated)
    resolved["status"] = "resolved"
    resolved["alerts"][0]["status"] = "resolved"  # type: ignore[index]
    resolved["alerts"][0]["endsAt"] = datetime.now(UTC).isoformat()  # type: ignore[index]
    resolved_response = _post(client, tokens, resolved)

    assert first.json()["counts"]["opened"] == 1  # type: ignore[attr-defined]
    assert update_response.json()["counts"]["updated"] == 1  # type: ignore[attr-defined]
    assert resolved_response.json()["counts"]["resolved"] == 1  # type: ignore[attr-defined]
    assert _row_count(migrated_engine, AlertRow) == 1
    with Session(migrated_engine) as session:
        assert session.scalar(select(AlertRow.state)) == "RESOLVED"


def test_invalid_item_rejects_the_whole_batch_without_writes(
    intake_client: tuple[TestClient, dict[str, str]],
    firing_payload: dict[str, object],
    migrated_engine: Engine,
) -> None:
    client, tokens = intake_client
    payload = deepcopy(firing_payload)
    invalid = deepcopy(firing_payload["alerts"][0])  # type: ignore[index]
    invalid["fingerprint"] = "fingerprint-invalid"
    invalid["status"] = "invalid"
    payload["alerts"] = [payload["alerts"][0], invalid]  # type: ignore[index]

    response = _post(client, tokens, payload)

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert response.json()["code"] == "validation_error"  # type: ignore[attr-defined]
    assert _row_count(migrated_engine, SignalEventRow) == 0
    assert _row_count(migrated_engine, AlertRow) == 0
    assert _row_count(migrated_engine, AuditEventRow) == 0


def test_experiment_identity_labels_are_accepted_without_persistence(
    intake_client: tuple[TestClient, dict[str, str]],
    firing_payload: dict[str, object],
    migrated_engine: Engine,
) -> None:
    client, tokens = intake_client
    payload = deepcopy(firing_payload)
    payload["alerts"][0]["labels"].update(  # type: ignore[index]
        {
            "scenario_id": "P0-DB-01",
            "experiment_id": "exp-123",
            "category": "fault-experiment",
        }
    )

    response = _post(client, tokens, payload)

    assert response.status_code == 202  # type: ignore[attr-defined]
    assert _row_count(migrated_engine, SignalEventRow) == 1
    with Session(migrated_engine) as session:
        facts = session.scalar(select(SignalEventRow.facts))
    assert facts is not None
    assert "scenario_id" not in facts
    assert "experiment_id" not in facts
    assert "category" not in facts


def test_source_event_conflict_uses_stable_safe_response(
    intake_client: tuple[TestClient, dict[str, str]],
    firing_payload: dict[str, object],
) -> None:
    client, tokens = intake_client

    class ConflictingService:
        def submit_batch(self, *args: object, **kwargs: object) -> None:
            raise SourceEventConflict()

    client.app.state.signal_intake_service = ConflictingService()
    response = _post(client, tokens, firing_payload)

    assert response.status_code == 409  # type: ignore[attr-defined]
    assert response.json() == {  # type: ignore[attr-defined]
        "code": "source_event_conflict",
        "message": "来源事件身份与已有内容冲突",
    }


def test_database_failure_is_safe_and_does_not_log_token_or_payload(
    intake_client: tuple[TestClient, dict[str, str]],
    firing_payload: dict[str, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, tokens = intake_client

    class FailingService:
        def submit_batch(self, *args: object, **kwargs: object) -> None:
            raise OperationalError("statement", {}, OSError("database unavailable"))

    client.app.state.signal_intake_service = FailingService()
    response = _post(client, tokens, firing_payload)

    assert response.status_code == 503  # type: ignore[attr-defined]
    assert response.json() == {  # type: ignore[attr-defined]
        "code": "persistence_unavailable",
        "message": "事故记录暂时无法保存。请稍后重试",
    }
    assert tokens["alertmanager"] not in caplog.text
    assert "支付接口错误率超过阈值" not in caplog.text
