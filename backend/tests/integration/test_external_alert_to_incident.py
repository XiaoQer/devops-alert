# ruff: noqa: RUF001

from __future__ import annotations

from datetime import UTC, datetime
from functools import partial

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.adapters.alertmanager import (
    AlertmanagerWebhook,
    to_signal_commands,
)
from incident_intelligence.persistence.models import (
    CorrelationDecisionRow,
    CorrelationJobRow,
    DiagnosisRunRow,
    IncidentAlertLinkRow,
    IncidentRow,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.catalog import CreateServiceCommand, ServiceCatalogService
from incident_intelligence.services.correlation import CorrelationService
from incident_intelligence.services.correlation_jobs import CorrelationJobService
from incident_intelligence.services.signal_intake import SignalIntakeService

NOW = datetime(2026, 8, 25, 8, 5, tzinfo=UTC)


def test_real_alertmanager_alert_becomes_explainable_incident_and_replay_is_idempotent(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
    catalog = ServiceCatalogService(uow_factory=uow_factory, clock=lambda: NOW)
    intake = SignalIntakeService(uow_factory=uow_factory, clock=lambda: NOW)
    jobs = CorrelationJobService(uow_factory=uow_factory)
    correlation = CorrelationService(uow_factory=uow_factory, clock=lambda: NOW)
    catalog.create_service(
        CreateServiceCommand(
            service="payment-api",
            environment="production",
            owner_team="payments",
        ),
        actor="manual-api-client",
        request_id="req-catalog",
    )
    webhook = AlertmanagerWebhook.model_validate(
        {
            "version": "4",
            "groupKey": "payment-errors",
            "truncatedAlerts": 0,
            "status": "firing",
            "receiver": "incident-intelligence",
            "groupLabels": {"service": "payment-api"},
            "commonLabels": {},
            "commonAnnotations": {},
            "externalURL": "https://alertmanager.example.com/cluster-a?token=discarded",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "PaymentHighErrorRate",
                        "service": "payment-api",
                        "environment": "production",
                        "severity": "high",
                        "symptom": "errors",
                    },
                    "annotations": {
                        "summary": "支付接口错误率升高",
                        "description": "错误率超过阈值",
                    },
                    "startsAt": "2026-08-25T08:00:00Z",
                    "endsAt": "0001-01-01T00:00:00Z",
                    "generatorURL": "https://prometheus.example.com/graph?query=discarded",
                    "fingerprint": "payment-errors-1",
                }
            ],
        }
    )
    commands = to_signal_commands(webhook, NOW)

    accepted = intake.submit_batch(
        commands,
        actor="alertmanager-adapter",
        request_id="req-first",
    )
    replayed = intake.submit_batch(
        commands,
        actor="alertmanager-adapter",
        request_id="req-replay",
    )
    lease = jobs.claim_batch("acceptance-runner", NOW, limit=10, lease_seconds=30)[0]
    decision = correlation.process(lease)

    assert accepted.items[0].outcome == "opened"
    assert replayed.items[0].replayed is True
    assert decision.outcome == "CREATED_NO_MATCH"
    assert decision.explanation == "窗口内没有同服务事故，已创建独立事故。"
    with session_factory() as session:
        assert _count(session, CorrelationJobRow) == 1
        assert _count(session, IncidentRow) == 1
        assert _count(session, IncidentAlertLinkRow) == 1
        assert _count(session, CorrelationDecisionRow) == 1
        assert _count(session, DiagnosisRunRow) == 0


def _count(session: Session, row_type: type[object]) -> int:
    value = session.scalar(select(func.count()).select_from(row_type))
    assert value is not None
    return value
