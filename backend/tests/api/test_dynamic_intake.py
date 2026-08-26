from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from incident_intelligence.main import create_app
from incident_intelligence.persistence.models import (
    AlertRow,
    AlertSourceReceiptRow,
    CorrelationJobRow,
    SignalEventRow,
    SignalIntakeResultRow,
)
from incident_intelligence.settings import Settings


@dataclass(frozen=True)
class DynamicIntakeContext:
    client: TestClient
    engine: Engine
    manual_headers: dict[str, str]


@pytest.fixture
def context(migrated_engine: Engine) -> Iterator[DynamicIntakeContext]:
    manual_token = token_urlsafe(32)
    app = create_app(
        Settings(
            database_url="mysql+pymysql://test-client@127.0.0.1/unused",
            api_token=SecretStr(manual_token),
            alertmanager_token=SecretStr(token_urlsafe(32)),
            cloudevents_token=SecretStr(token_urlsafe(32)),
            correlation_runner_enabled=False,
        ),
        engine=migrated_engine,
    )
    with TestClient(app) as client:
        try:
            yield DynamicIntakeContext(
                client=client,
                engine=migrated_engine,
                manual_headers={"Authorization": f"Bearer {manual_token}"},
            )
        finally:
            with Session(migrated_engine) as session:
                session.execute(delete(CorrelationJobRow))
                session.execute(delete(AlertSourceReceiptRow))
                session.execute(delete(SignalIntakeResultRow))
                session.execute(delete(AlertRow))
                session.execute(delete(SignalEventRow))
                session.commit()


def _create_source(context: DynamicIntakeContext, name: str, source_type: str):
    response = context.client.post(
        "/api/v1/alert-sources",
        headers={
            **context.manual_headers,
            "Idempotency-Key": f"create-{sha256(name.encode()).hexdigest()[:16]}",
        },
        json={"name": name, "source_type": source_type},
    )
    assert response.status_code == 201
    return response.json()


def _alertmanager_payload() -> dict[str, object]:
    started_at = datetime.now(UTC) - timedelta(minutes=1)
    return {
        "version": "4",
        "groupKey": '{}:{alertname="PaymentHighErrorRate"}',
        "truncatedAlerts": 0,
        "status": "firing",
        "receiver": "incident-intelligence",
        "groupLabels": {"alertname": "PaymentHighErrorRate"},
        "commonLabels": {"service": "payment-api"},
        "commonAnnotations": {},
        "externalURL": "https://alertmanager.example.com/cluster-a",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "PaymentHighErrorRate",
                    "service": "payment-api",
                    "environment": "production",
                    "severity": "high",
                },
                "annotations": {
                    "summary": "支付接口错误率升高",
                    "description": "支付接口错误率超过阈值",
                },
                "startsAt": started_at.isoformat(),
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "https://prometheus.example.com/graph",
                "fingerprint": "same-fingerprint",
            }
        ],
    }


def _cloudevent_payload() -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "specversion": "1.0",
        "id": "dynamic-cloud-event-1",
        "source": "https://events.example.com/monitor-a",
        "type": "com.incidentintelligence.alert.v1",
        "subject": "payment-api",
        "time": now.isoformat(),
        "datacontenttype": "application/json",
        "data": {
            "alert_key": "payment-error-rate",
            "title": "支付接口错误率升高",
            "summary": "支付接口错误率超过阈值",
            "severity": "high",
            "service": "payment-api",
            "environment": "production",
            "status": "firing",
            "started_at": (now - timedelta(minutes=1)).isoformat(),
            "labels": {"region": "cn-east-1"},
        },
    }


def _count(engine: Engine, row_type: type[object]) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(row_type)) or 0


def test_two_registered_sources_isolate_same_external_alert_and_record_receipts(
    context: DynamicIntakeContext,
) -> None:
    first = _create_source(context, "Alertmanager A", "ALERTMANAGER")
    second = _create_source(context, "Alertmanager B", "ALERTMANAGER")
    payload = _alertmanager_payload()

    first_response = context.client.post(
        f"/api/v1/intake/alertmanager/{first['source']['id']}",
        headers={"Authorization": f"Bearer {first['token']}"},
        json=payload,
    )
    second_response = context.client.post(
        f"/api/v1/intake/alertmanager/{second['source']['id']}",
        headers={"Authorization": f"Bearer {second['token']}"},
        json=payload,
    )

    assert first_response.status_code == 202, first_response.text
    assert second_response.status_code == 202, second_response.text
    assert (
        first_response.json()["items"][0]["alert_id"]
        != second_response.json()["items"][0]["alert_id"]
    )
    assert _count(context.engine, SignalEventRow) == 2
    assert _count(context.engine, AlertRow) == 2
    assert _count(context.engine, CorrelationJobRow) == 2
    assert _count(context.engine, AlertSourceReceiptRow) == 2


def test_validation_writes_receipt_without_domain_records(
    context: DynamicIntakeContext,
) -> None:
    source = _create_source(context, "验证来源", "ALERTMANAGER")
    response = context.client.post(
        f"/api/v1/intake/alertmanager/{source['source']['id']}/validate",
        headers={"Authorization": f"Bearer {source['token']}"},
        json=_alertmanager_payload(),
    )
    assert response.status_code == 202
    assert response.json() == {"valid": True, "input_count": 1}
    assert _count(context.engine, AlertSourceReceiptRow) == 1
    assert _count(context.engine, SignalEventRow) == 0
    assert _count(context.engine, AlertRow) == 0
    assert _count(context.engine, CorrelationJobRow) == 0


def test_cross_source_token_is_rejected_without_receipt(
    context: DynamicIntakeContext,
) -> None:
    first = _create_source(context, "来源 A", "ALERTMANAGER")
    second = _create_source(context, "来源 B", "ALERTMANAGER")
    response = context.client.post(
        f"/api/v1/intake/alertmanager/{second['source']['id']}",
        headers={"Authorization": f"Bearer {first['token']}"},
        json=_alertmanager_payload(),
    )
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"
    assert first["token"] not in response.text
    assert _count(context.engine, AlertSourceReceiptRow) == 0


def test_authenticated_invalid_payload_and_type_mismatch_write_safe_receipts(
    context: DynamicIntakeContext,
) -> None:
    alertmanager = _create_source(context, "格式失败", "ALERTMANAGER")
    cloudevents = _create_source(context, "类型不符", "CLOUDEVENTS")
    invalid = context.client.post(
        f"/api/v1/intake/alertmanager/{alertmanager['source']['id']}",
        headers={"Authorization": f"Bearer {alertmanager['token']}"},
        json={"invalid": "payload"},
    )
    mismatch = context.client.post(
        f"/api/v1/intake/alertmanager/{cloudevents['source']['id']}",
        headers={"Authorization": f"Bearer {cloudevents['token']}"},
        json=_alertmanager_payload(),
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "invalid_source_payload"
    assert mismatch.status_code == 409
    assert mismatch.json()["code"] == "alert_source_type_mismatch"
    with Session(context.engine) as session:
        reasons = set(session.scalars(select(AlertSourceReceiptRow.reason_code)))
    assert reasons == {"invalid_source_payload", "alert_source_type_mismatch"}
    assert _count(context.engine, SignalEventRow) == 0


def test_registered_cloudevents_source_accepts_structured_event(
    context: DynamicIntakeContext,
) -> None:
    source = _create_source(context, "CloudEvents 来源", "CLOUDEVENTS")
    response = context.client.post(
        f"/api/v1/intake/cloudevents/{source['source']['id']}",
        headers={
            "Authorization": f"Bearer {source['token']}",
            "Content-Type": "application/cloudevents+json",
        },
        json=_cloudevent_payload(),
    )
    assert response.status_code == 202
    assert response.json()["counts"]["opened"] == 1
    with Session(context.engine) as session:
        receipt = session.scalar(select(AlertSourceReceiptRow))
    assert receipt is not None
    assert receipt.adapter_type == "CLOUDEVENTS"
    assert receipt.outcome == "ACCEPTED"


def test_disabled_and_revoked_credentials_stop_new_data_immediately(
    context: DynamicIntakeContext,
) -> None:
    created = _create_source(context, "生命周期来源", "ALERTMANAGER")
    source_id = created["source"]["id"]
    credential_id = created["credential_id"]
    token = created["token"]
    disabled = context.client.patch(
        f"/api/v1/alert-sources/{source_id}",
        headers={**context.manual_headers, "Idempotency-Key": "disable-source"},
        json={"expected_version": 1, "state": "DISABLED"},
    )
    assert disabled.status_code == 200
    rejected = context.client.post(
        f"/api/v1/intake/alertmanager/{source_id}",
        headers={"Authorization": f"Bearer {token}"},
        json=_alertmanager_payload(),
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "alert_source_disabled"

    enabled = context.client.patch(
        f"/api/v1/alert-sources/{source_id}",
        headers={**context.manual_headers, "Idempotency-Key": "enable-source"},
        json={"expected_version": 2, "state": "ENABLED"},
    )
    assert enabled.status_code == 200
    rotated = context.client.post(
        f"/api/v1/alert-sources/{source_id}/credentials/rotate",
        headers={**context.manual_headers, "Idempotency-Key": "rotate-source"},
        json={"expected_version": 3},
    )
    assert rotated.status_code == 200
    revoked = context.client.post(
        f"/api/v1/alert-sources/{source_id}/credentials/{credential_id}/revoke",
        headers={**context.manual_headers, "Idempotency-Key": "revoke-source"},
        json={"expected_version": 4},
    )
    assert revoked.status_code == 200
    old_token = context.client.post(
        f"/api/v1/intake/alertmanager/{source_id}",
        headers={"Authorization": f"Bearer {token}"},
        json=_alertmanager_payload(),
    )
    assert old_token.status_code == 401
    assert old_token.json()["code"] == "authentication_required"
    with Session(context.engine) as session:
        outcomes = list(session.scalars(select(AlertSourceReceiptRow.outcome)))
    assert outcomes == ["SOURCE_DISABLED"]


def test_dynamic_routes_keep_adapter_specific_body_limits(
    context: DynamicIntakeContext,
) -> None:
    alertmanager = _create_source(context, "容量 Alertmanager", "ALERTMANAGER")
    cloudevents = _create_source(context, "容量 CloudEvents", "CLOUDEVENTS")
    alertmanager_response = context.client.post(
        f"/api/v1/intake/alertmanager/{alertmanager['source']['id']}",
        headers={
            "Authorization": f"Bearer {alertmanager['token']}",
            "Content-Type": "application/json",
        },
        content=b"x" * 262_145,
    )
    cloudevents_response = context.client.post(
        f"/api/v1/intake/cloudevents/{cloudevents['source']['id']}",
        headers={
            "Authorization": f"Bearer {cloudevents['token']}",
            "Content-Type": "application/cloudevents+json",
        },
        content=b"x" * 65_537,
    )
    assert alertmanager_response.status_code == 413
    assert cloudevents_response.status_code == 413
    assert alertmanager_response.json()["code"] == "request_too_large"
    assert cloudevents_response.json()["code"] == "request_too_large"
