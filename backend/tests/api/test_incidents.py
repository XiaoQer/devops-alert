from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from secrets import token_urlsafe

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.signal_intake import SignalCommand
from incident_intelligence.main import create_app
from incident_intelligence.persistence.models import (
    AuditEventRow,
    IncidentActivityRow,
    IncidentOperationRow,
    IncidentRow,
)
from incident_intelligence.persistence.session import make_session_factory
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.catalog import CreateServiceCommand, ServiceCatalogService
from incident_intelligence.services.correlation import CorrelationService
from incident_intelligence.services.correlation_jobs import CorrelationJobService
from incident_intelligence.services.signal_intake import SignalIntakeService
from incident_intelligence.settings import Settings

NOW = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)


@dataclass(frozen=True)
class IncidentApiContext:
    client: TestClient
    manual_headers: dict[str, str]
    alertmanager_headers: dict[str, str]
    catalog: ServiceCatalogService
    intake: SignalIntakeService
    jobs: CorrelationJobService
    correlation: CorrelationService
    session_factory: sessionmaker[Session]


@pytest.fixture
def context(migrated_engine: Engine) -> Iterator[IncidentApiContext]:
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
    session_factory = make_session_factory(migrated_engine)
    uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
    with TestClient(app) as client:
        yield IncidentApiContext(
            client=client,
            manual_headers={"Authorization": f"Bearer {manual_token}"},
            alertmanager_headers={"Authorization": f"Bearer {alertmanager_token}"},
            catalog=ServiceCatalogService(uow_factory=uow_factory, clock=lambda: NOW),
            intake=SignalIntakeService(uow_factory=uow_factory, clock=lambda: NOW),
            jobs=CorrelationJobService(uow_factory=uow_factory),
            correlation=CorrelationService(uow_factory=uow_factory, clock=lambda: NOW),
            session_factory=session_factory,
        )


def seed_linked_incident(context: IncidentApiContext) -> str:
    context.catalog.create_service(
        CreateServiceCommand(
            service="payment-api", environment="production", owner_team="支付平台组"
        ),
        actor="manual-api-client",
        request_id="req-catalog",
    )
    incident_id: str | None = None
    for index, event_at in enumerate((NOW, NOW + timedelta(minutes=1)), start=1):
        result = context.intake.submit_batch(
            [
                SignalCommand(
                    alert_source_id="src_00000000000000000000000000000002",
                    source="alertmanager",
                    source_instance="a" * 64,
                    source_event_id=f"{index:064x}",
                    source_alert_key=f"payment-alert-{index}",
                    event_type="alert.firing",
                    event_at=event_at,
                    episode_started_at=event_at,
                    title=f"支付告警 {index}",
                    summary="只用于真实聚合测试",
                    severity="high" if index == 1 else "critical",
                    service="payment-api",
                    environment="production",
                    facts={"symptom": "errors"},
                )
            ],
            actor="alertmanager-adapter",
            request_id=f"req-alert-{index}",
        )
        alert_id = result.items[0].alert_id
        assert alert_id is not None
        lease = context.jobs.claim_batch(
            "incident-api-runner", event_at, limit=1, lease_seconds=30
        )[0]
        correlation = context.correlation.process(lease)
        incident_id = correlation.incident_id
    assert incident_id is not None
    return incident_id


def post_operation(
    context: IncidentApiContext,
    incident_id: str,
    suffix: str,
    version: int,
    body: dict[str, object] | None = None,
    *,
    key: str | None = None,
    headers: dict[str, str] | None = None,
) -> object:
    request_headers = {
        **(context.manual_headers if headers is None else headers),
        "Idempotency-Key": key or f"{suffix}-{version}",
    }
    return context.client.post(
        f"/api/v1/incidents/{incident_id}/{suffix}",
        headers=request_headers,
        json={"expected_version": version, **(body or {})},
    )


def test_list_and_overview_return_real_bounded_aggregate(
    context: IncidentApiContext,
) -> None:
    incident_id = seed_linked_incident(context)

    listed = context.client.get(
        "/api/v1/incidents?environment=production&query=payment&limit=20&offset=0",
        headers=context.manual_headers,
    )
    detail = context.client.get(
        f"/api/v1/incidents/{incident_id}/overview",
        headers=context.manual_headers,
    )

    assert listed.status_code == 200
    assert set(listed.json()) == {"items", "total", "limit", "offset"}
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["alert_count"] == 2
    assert listed.json()["items"][0]["owner_team"] == "支付平台组"
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == incident_id
    assert body["state_changed_at"] is not None
    assert body["resolved_at"] is None
    assert body["closed_at"] is None
    assert body["activities"] == []
    assert body["activities_truncated"] is False
    assert "TRANSITION" in body["allowed_actions"]
    assert body["allowed_transitions"] == ["TRIAGING", "INVESTIGATING"]
    assert body["primary_action"] == {
        "action": "TRANSITION",
        "target_state": "TRIAGING",
    }
    assert len(body["alerts"]) == 2
    assert body["correlation"]["rule_version"] == "correlation.v1"
    assert body["correlation"]["explanation"] == "窗口内只有一个同服务事故，已自动关联。"  # noqa: RUF001
    assert [event["kind"] for event in body["timeline"]][:2] == [
        "incident_created",
        "alert_linked",
    ]
    assert "summary" not in detail.text
    assert "facts" not in detail.text
    assert "source_event_id" not in detail.text


def test_claim_is_persisted_idempotent_and_audited_once(
    context: IncidentApiContext,
) -> None:
    incident_id = seed_linked_incident(context)
    with context.session_factory() as session:
        incident = session.get(IncidentRow, incident_id)
        assert incident is not None
        version = incident.version

    first = post_operation(context, incident_id, "claim", version, key="claim-replay")
    replay = post_operation(context, incident_id, "claim", version, key="claim-replay")

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["assignee"] == "manual-api-client"
    assert first.json()["action"] == "CLAIM"
    assert first.json()["version"] == version + 1
    with context.session_factory() as session:
        incident = session.get(IncidentRow, incident_id)
        assert incident is not None
        assert incident.assignee == "manual-api-client"
        audits = tuple(
            session.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.resource_id == incident_id,
                    AuditEventRow.action == "incident.claimed",
                )
            )
        )
        assert len(audits) == 1
        assert set(audits[0].details) == {"reason_code", "activity_id"}
        assert (
            session.scalar(
                select(IncidentActivityRow).where(IncidentActivityRow.incident_id == incident_id)
            )
            is not None
        )
        assert (
            session.scalar(
                select(IncidentOperationRow).where(IncidentOperationRow.incident_id == incident_id)
            )
            is not None
        )


def test_incident_operation_http_journey(context: IncidentApiContext) -> None:
    incident_id = seed_linked_incident(context)
    with context.session_factory() as session:
        incident = session.get(IncidentRow, incident_id)
        assert incident is not None
        version = incident.version

    steps = (
        ("claim", {}, "CLAIM", "DETECTED"),
        (
            "transitions",
            {"target_state": "INVESTIGATING", "message": "开始调查"},
            "TRANSITION",
            "INVESTIGATING",
        ),
        (
            "notes",
            {"category": "CURRENT_FINDING", "message": "错误集中在两个实例"},
            "ADD_NOTE",
            "INVESTIGATING",
        ),
        (
            "transitions",
            {"target_state": "MITIGATING", "message": "隔离异常实例"},
            "TRANSITION",
            "MITIGATING",
        ),
        (
            "transitions",
            {"target_state": "MONITORING_RECOVERY", "message": "观察恢复指标"},
            "TRANSITION",
            "MONITORING_RECOVERY",
        ),
        (
            "resolve",
            {
                "category": "RECOVERED",
                "message": "错误率恢复正常",
                "resolution_actions": "隔离异常实例并扩容",
                "root_cause": None,
            },
            "RESOLVE",
            "RESOLVED",
        ),
        ("reopen", {"reason": "错误率再次升高"}, "REOPEN", "INVESTIGATING"),
        (
            "resolve",
            {
                "category": "RECOVERED",
                "message": "服务再次恢复",
                "resolution_actions": "完成配置修正",
                "root_cause": "实例配置不一致",
            },
            "RESOLVE",
            "RESOLVED",
        ),
        ("close", {"message": "复盘完成并关闭事故"}, "CLOSE", "CLOSED"),
    )
    for index, (suffix, body, action, state) in enumerate(steps, start=1):
        response = post_operation(
            context,
            incident_id,
            suffix,
            version,
            body,
            key=f"journey-{index}",
        )
        assert response.status_code == 200, response.text
        result = response.json()
        version += 1
        assert result["action"] == action
        assert result["state"] == state
        assert result["version"] == version
        assert result["activity_id"].startswith("iact_")

    with context.session_factory() as session:
        incident = session.get(IncidentRow, incident_id)
        assert incident is not None
        assert incident.state == "CLOSED"
        assert incident.resolved_at is not None
        assert incident.closed_at is not None
        activities = tuple(
            session.scalars(
                select(IncidentActivityRow).where(IncidentActivityRow.incident_id == incident_id)
            )
        )
        assert len(activities) == len(steps)
        assert sum(item.kind == "INCIDENT_RESOLVED" for item in activities) == 2


@pytest.mark.parametrize(
    "suffix",
    ["claim", "release", "transitions", "notes", "resolve", "reopen", "close"],
)
def test_all_operation_routes_require_manual_token_and_idempotency_key(
    context: IncidentApiContext,
    suffix: str,
) -> None:
    incident_id = "inc_" + "0" * 32
    without_token = post_operation(
        context,
        incident_id,
        suffix,
        1,
        headers={},
    )
    without_key = context.client.post(
        f"/api/v1/incidents/{incident_id}/{suffix}",
        headers=context.manual_headers,
        json={"expected_version": 1},
    )
    assert without_token.status_code == 401
    assert without_token.json()["code"] == "authentication_required"
    assert without_key.status_code == 400
    assert without_key.json()["code"] == "invalid_idempotency_key"


def test_operation_conflicts_return_stable_safe_errors(
    context: IncidentApiContext,
) -> None:
    incident_id = seed_linked_incident(context)
    stale = post_operation(context, incident_id, "claim", 99, key="stale")
    assert stale.status_code == 409
    assert stale.json() == {
        "code": "incident_version_conflict",
        "message": "事故已被其他操作更新，请刷新后重试",  # noqa: RUF001
    }

    with context.session_factory() as session:
        incident = session.get(IncidentRow, incident_id)
        assert incident is not None
        version = incident.version
    first = post_operation(
        context,
        incident_id,
        "notes",
        version,
        {"category": "GENERAL", "message": "第一条记录"},
        key="conflict-key",
    )
    conflict = post_operation(
        context,
        incident_id,
        "notes",
        version,
        {"category": "GENERAL", "message": "另一条记录"},
        key="conflict-key",
    )
    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "incident_operation_conflict"


@pytest.mark.parametrize(
    ("suffix", "body"),
    [
        ("transitions", {"target_state": "INVESTIGATING", "message": "x" * 1_001}),
        ("notes", {"category": "GENERAL", "message": "x" * 2_001}),
        (
            "resolve",
            {
                "category": "RECOVERED",
                "message": "已恢复",
                "resolution_actions": "x" * 4_001,
                "root_cause": None,
            },
        ),
        ("notes", {"category": "ARBITRARY", "message": "非法分类"}),
        ("claim", {"actor": "browser-user"}),
        ("claim", {"assignee": "browser-user"}),
    ],
)
def test_operation_request_fields_are_bounded_and_cannot_report_actor(
    context: IncidentApiContext,
    suffix: str,
    body: dict[str, object],
) -> None:
    response = post_operation(
        context,
        "inc_" + "0" * 32,
        suffix,
        1,
        body,
        key=f"invalid-{suffix}-{len(str(body))}",
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/incidents",
        "/api/v1/incidents/inc_00000000000000000000000000000000/overview",
        "/api/v1/incidents/inc_00000000000000000000000000000000/claim",
    ],
)
def test_incident_center_rejects_non_manual_credentials(
    context: IncidentApiContext,
    path: str,
) -> None:
    method = context.client.post if path.endswith("/claim") else context.client.get
    response = method(path, headers=context.alertmanager_headers)
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


def test_incident_list_and_ids_are_bounded(context: IncidentApiContext) -> None:
    invalid_limit = context.client.get(
        "/api/v1/incidents?limit=101", headers=context.manual_headers
    )
    invalid_query = context.client.get(
        f"/api/v1/incidents?query={'x' * 101}", headers=context.manual_headers
    )
    missing = context.client.get(
        "/api/v1/incidents/not-an-id/overview", headers=context.manual_headers
    )
    assert invalid_limit.status_code == 422
    assert invalid_query.status_code == 422
    assert missing.status_code == 404
    assert missing.json()["code"] == "resource_not_found"
