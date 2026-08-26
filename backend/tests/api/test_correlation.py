# ruff: noqa: RUF001

from __future__ import annotations

import asyncio
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
from incident_intelligence.persistence.models import AuditEventRow
from incident_intelligence.persistence.session import make_session_factory
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.catalog import CreateServiceCommand, ServiceCatalogService
from incident_intelligence.services.correlation import CorrelationService
from incident_intelligence.services.correlation_jobs import CorrelationJobService
from incident_intelligence.services.signal_intake import SignalIntakeService
from incident_intelligence.settings import Settings

NOW = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)


@dataclass(frozen=True)
class CorrelationApiContext:
    client: TestClient
    manual_headers: dict[str, str]
    alertmanager_headers: dict[str, str]
    catalog: ServiceCatalogService
    intake: SignalIntakeService
    jobs: CorrelationJobService
    correlation: CorrelationService
    session_factory: sessionmaker[Session]


@pytest.fixture
def context(migrated_engine: Engine) -> Iterator[CorrelationApiContext]:
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
    session_factory = make_session_factory(migrated_engine)
    uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
    with TestClient(app) as client:
        yield CorrelationApiContext(
            client=client,
            manual_headers={"Authorization": f"Bearer {manual_token}"},
            alertmanager_headers={"Authorization": f"Bearer {alertmanager_token}"},
            catalog=ServiceCatalogService(uow_factory=uow_factory, clock=lambda: NOW),
            intake=SignalIntakeService(uow_factory=uow_factory, clock=lambda: NOW),
            jobs=CorrelationJobService(uow_factory=uow_factory),
            correlation=CorrelationService(uow_factory=uow_factory, clock=lambda: NOW),
            session_factory=session_factory,
        )


def seed_decision(context: CorrelationApiContext) -> tuple[str, str]:
    context.catalog.create_service(
        CreateServiceCommand(
            service="payment-api", environment="production", owner_team="payments"
        ),
        actor="manual-api-client",
        request_id="req-catalog",
    )
    intake_result = context.intake.submit_batch(
        [
            SignalCommand(
                alert_source_id="src_00000000000000000000000000000002",
                source="alertmanager",
                source_instance="a" * 64,
                source_event_id="b" * 64,
                source_alert_key="payment-errors",
                event_type="alert.firing",
                event_at=NOW,
                episode_started_at=NOW,
                title="支付错误率升高",
                summary="原始摘要不得出现在关联读取响应",
                severity="high",
                service="payment-api",
                environment="production",
                facts={"symptom": "errors", "private": "不得返回"},
            )
        ],
        actor="alertmanager-adapter",
        request_id="req-alert",
    )
    alert_id = intake_result.items[0].alert_id
    assert alert_id is not None
    _enqueue_legacy_job(context, alert_id)
    lease = context.jobs.claim_batch("api-test-runner", NOW, limit=1, lease_seconds=30)[0]
    result = context.correlation.process(lease)
    return alert_id, result.decision_id


def test_alert_correlation_response_is_bounded_and_explainable(
    context: CorrelationApiContext,
) -> None:
    alert_id, _ = seed_decision(context)

    response = context.client.get(
        f"/api/v1/alerts/{alert_id}/correlation",
        headers=context.manual_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"alert_id", "job", "incident", "decision"}
    assert body["decision"]["rule_version"] == "correlation.v1"
    assert body["decision"]["explanation"] == "窗口内没有同服务事故，已创建独立事故。"
    serialized = response.text
    assert "lease_owner" not in serialized
    assert "支付错误率升高" not in serialized
    assert "原始摘要" not in serialized
    assert "private" not in serialized


@pytest.mark.parametrize("headers_name", ["none", "alertmanager"])
def test_correlation_endpoints_only_accept_manual_token(
    context: CorrelationApiContext,
    headers_name: str,
) -> None:
    headers = {} if headers_name == "none" else context.alertmanager_headers
    response = context.client.get("/api/v1/correlation/jobs", headers=headers)
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


def test_only_failed_job_can_be_retried_and_list_is_bounded(
    context: CorrelationApiContext,
) -> None:
    alert_id, _ = seed_decision(context)
    detail = context.client.get(
        f"/api/v1/alerts/{alert_id}/correlation", headers=context.manual_headers
    ).json()
    job_id = detail["job"]["id"]

    conflict = context.client.post(
        f"/api/v1/correlation/jobs/{job_id}/retry",
        headers=context.manual_headers,
    )
    invalid_limit = context.client.get(
        "/api/v1/correlation/jobs?limit=101", headers=context.manual_headers
    )
    listed = context.client.get(
        "/api/v1/correlation/jobs?limit=100&offset=0", headers=context.manual_headers
    )

    assert conflict.status_code == 409
    assert conflict.json()["code"] == "correlation_job_not_retryable"
    assert invalid_limit.status_code == 422
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == job_id
    assert "lease_owner" not in listed.text


def test_failed_job_retry_resets_attempts_and_writes_bounded_audit(
    context: CorrelationApiContext,
) -> None:
    intake_result = context.intake.submit_batch(
        [
            SignalCommand(
                alert_source_id="src_00000000000000000000000000000002",
                source="alertmanager",
                source_instance="c" * 64,
                source_event_id="d" * 64,
                source_alert_key="retry-alert",
                event_type="alert.firing",
                event_at=NOW,
                episode_started_at=NOW,
                title="重试测试",
                summary="重试测试",
                severity="high",
                service="unregistered-service",
                environment="production",
                facts={},
            )
        ],
        actor="alertmanager-adapter",
        request_id="req-retry-alert",
    )
    alert_id = intake_result.items[0].alert_id
    assert alert_id is not None
    _enqueue_legacy_job(context, alert_id)
    lease = None
    for attempt in range(5):
        current = NOW + timedelta(minutes=attempt * 10)
        lease = context.jobs.claim_batch("failing-runner", current, limit=1, lease_seconds=30)[0]
        context.jobs.fail(
            lease.id,
            "failing-runner",
            "correlation_processing_failed",
            current,
        )
    assert lease is not None

    response = context.client.post(
        f"/api/v1/correlation/jobs/{lease.id}/retry",
        headers=context.manual_headers,
    )

    assert response.status_code == 200
    assert response.json()["state"] == "PENDING"
    assert response.json()["attempts"] == 0
    with context.session_factory() as session:
        audit = session.scalar(
            select(AuditEventRow).where(AuditEventRow.action == "correlation.job_retried")
        )
    assert audit is not None
    assert audit.details == {"reason_code": "manual_retry_requested"}


def _enqueue_legacy_job(context: CorrelationApiContext, alert_id: str) -> None:
    with SqlAlchemyUnitOfWork(context.session_factory) as uow:
        assert uow.correlation is not None
        alert = uow.correlation.find_alert_for_update(alert_id)
        assert alert is not None
        uow.correlation.enqueue(
            alert_source_id=alert.alert_source_id,
            alert_id=alert.id,
            alert_version=alert.version,
            now=NOW,
        )
        uow.commit()


def test_enabled_runner_lifespan_starts_and_stops_cleanly(
    migrated_engine: Engine,
) -> None:
    class TrackingRunner:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False

        async def run_forever(self) -> None:
            self.started = True
            while not self.stopped:
                await asyncio.sleep(0)

        async def stop(self) -> None:
            self.stopped = True

    settings = Settings(
        database_url="mysql+pymysql://test-client@127.0.0.1/unused",
        api_token=SecretStr("manual-token-value"),
        alertmanager_token=SecretStr("alertmanager-token-value"),
        cloudevents_token=SecretStr("cloudevents-token-value"),
        correlation_runner_enabled=True,
    )
    app = create_app(settings, engine=migrated_engine)
    runner = TrackingRunner()
    app.state.correlation_runner = runner

    with TestClient(app) as client:
        client.get("/health/live")
        assert runner.started is True

    assert runner.stopped is True


def test_correlation_master_switch_disables_all_three_processing_loops(
    migrated_engine: Engine,
) -> None:
    class TrackingRunner:
        def __init__(self) -> None:
            self.started = False

        async def run_forever(self) -> None:
            self.started = True

        async def stop(self) -> None:
            return None

    settings = Settings(
        database_url="mysql+pymysql://test-client@127.0.0.1/unused",
        api_token=SecretStr("manual-token-value"),
        alertmanager_token=SecretStr("alertmanager-token-value"),
        cloudevents_token=SecretStr("cloudevents-token-value"),
        correlation_runner_enabled=False,
        alert_grouping_runner_enabled=True,
    )
    app = create_app(settings, engine=migrated_engine)
    old_correlation = TrackingRunner()
    grouping = TrackingRunner()
    group_correlation = TrackingRunner()
    app.state.correlation_runner = old_correlation
    app.state.alert_grouping_runner = grouping
    app.state.alert_group_correlation_runner = group_correlation

    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200

    assert old_correlation.started is False
    assert grouping.started is False
    assert group_correlation.started is False
