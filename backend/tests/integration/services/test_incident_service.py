# ruff: noqa: RUF001

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from incident_intelligence.domain.alert_sources import ALERTMANAGER_COMPAT_SOURCE_ID
from incident_intelligence.domain.incident_rules import IncidentRuleConfig, create_rule
from incident_intelligence.domain.incidents import (
    IncidentAlertFact,
    IncidentAlertLink,
    create_incident,
)
from incident_intelligence.persistence.incident_repository import IncidentRepository
from incident_intelligence.persistence.incident_rule_repository import IncidentRuleRepository
from incident_intelligence.persistence.models import (
    AlertLifecycleRow,
    IncidentNotificationOutboxRow,
    OperationalIncidentActivityRow,
    OperationalIncidentOperationRow,
)
from incident_intelligence.persistence.session import make_session_factory
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.incidents import (
    IncidentIdempotencyConflict,
    IncidentService,
    IncidentVersionConflict,
)

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
INCIDENT_ID = "inc_11111111111111111111111111111111"
RULE_ID = "irl_22222222222222222222222222222222"
ALERT_ID = "alt_33333333333333333333333333333333"


def test_acknowledge_is_persistently_idempotent_and_writes_one_activity(
    migrated_engine: Engine,
) -> None:
    _seed_incident(migrated_engine)
    ids = iter(
        (
            "iact_44444444444444444444444444444444",
            "ino_55555555555555555555555555555555",
            "iop_66666666666666666666666666666666",
        )
    )
    service = IncidentService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
        clock=lambda: NOW,
        id_factory=lambda prefix: next(ids),
    )

    first = service.acknowledge(
        INCIDENT_ID,
        expected_version=1,
        idempotency_key="ack-1",
        actor="tester",
        request_id="req-1",
    )
    replay = service.acknowledge(
        INCIDENT_ID,
        expected_version=1,
        idempotency_key="ack-1",
        actor="tester",
        request_id="req-2",
    )

    assert first.replayed is False
    assert first.incident.state == "ACKNOWLEDGED"
    assert replay.replayed is True
    assert replay.incident.version == 2
    with Session(migrated_engine) as session:
        acknowledged_count = session.scalar(
            select(func.count())
            .select_from(OperationalIncidentActivityRow)
            .where(OperationalIncidentActivityRow.kind == "ACKNOWLEDGED")
        )
        assert acknowledged_count == 1
        assert session.scalar(select(func.count()).select_from(IncidentNotificationOutboxRow)) == 1
        assert (
            session.scalar(select(func.count()).select_from(OperationalIncidentOperationRow)) == 1
        )


def test_same_idempotency_key_with_changed_command_is_rejected(
    migrated_engine: Engine,
) -> None:
    _seed_incident(migrated_engine)
    service = IncidentService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
        clock=lambda: NOW,
    )
    service.acknowledge(
        INCIDENT_ID,
        expected_version=1,
        idempotency_key="same-key",
        actor="tester",
        request_id="req-1",
    )

    with pytest.raises(IncidentIdempotencyConflict):
        service.acknowledge(
            INCIDENT_ID,
            expected_version=2,
            idempotency_key="same-key",
            actor="tester",
            request_id="req-2",
        )


def test_resolve_rejects_stale_version(migrated_engine: Engine) -> None:
    _seed_incident(migrated_engine)
    service = IncidentService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
        clock=lambda: NOW,
    )

    with pytest.raises(IncidentVersionConflict):
        service.resolve(
            INCIDENT_ID,
            expected_version=99,
            resolution_summary="服务已恢复",
            idempotency_key="resolve-1",
            actor="tester",
            request_id="req-1",
        )


def test_list_defaults_to_unresolved_incidents_and_includes_rule_context(
    migrated_engine: Engine,
) -> None:
    _seed_incident(migrated_engine)
    service = IncidentService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine))
    )

    page = service.list()

    assert page.total == 1
    assert page.items[0].id == INCIDENT_ID
    assert page.items[0].rule_name == "支付链路异常"
    assert page.items[0].rule_summary == (
        "生产环境内，按同一服务分组，在 5 分钟内：不同 Alertname 数量不少于 1"
    )


def test_get_returns_bounded_safe_incident_detail(migrated_engine: Engine) -> None:
    _seed_incident(migrated_engine)
    service = IncidentService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine))
    )

    detail = service.get(INCIDENT_ID)

    assert detail.incident.id == INCIDENT_ID
    assert detail.rule_name == "支付链路异常"
    assert detail.alerts[0].alert_name == "HighErrorRate"
    assert detail.alerts[0].source_name == "Alertmanager 兼容接入"
    assert detail.activities[0].kind == "INCIDENT_CREATED"
    assert detail.alerts_truncated is False
    assert detail.activities_truncated is False
    assert detail.feishu.configured is False
    assert detail.feishu.thread_bound is False


def _seed_incident(engine: Engine) -> None:
    with Session(engine) as session:
        rule = create_rule(
            rule_id=RULE_ID,
            name="支付链路异常",
            description="测试规则",
            config=IncidentRuleConfig.model_validate(
                {
                    "environment": "production",
                    "group_by": "SERVICE",
                    "window_minutes": 5,
                    "conditions": ({"type": "DISTINCT_ALERT_NAMES_GTE", "threshold": 1},),
                }
            ),
            now=NOW,
        )
        IncidentRuleRepository(session).insert(rule)
        session.add(_alert())
        change = create_incident(
            incident_id=INCIDENT_ID,
            reference="INC-20260901-001",
            rule_id=RULE_ID,
            rule_version=1,
            environment="production",
            group_by="SERVICE",
            group_key="checkout",
            group_display_name="checkout",
            alerts=(
                IncidentAlertFact(
                    id=ALERT_ID,
                    alert_name="HighErrorRate",
                    state="ACTIVE",
                    severity="high",
                    first_received_at=NOW,
                ),
            ),
            created_activity_id="iact_77777777777777777777777777777777",
            now=NOW,
        )
        incidents = IncidentRepository(session)
        incidents.insert(change.incident)
        incidents.link_alerts(
            (
                IncidentAlertLink(
                    incident_id=INCIDENT_ID,
                    alert_id=ALERT_ID,
                    incident_rule_version=1,
                    first_trigger_window=True,
                    linked_at=NOW,
                ),
            )
        )
        incidents.append_activities(change.activities)
        session.commit()


def _alert() -> AlertLifecycleRow:
    return AlertLifecycleRow(
        id=ALERT_ID,
        alert_source_id=ALERTMANAGER_COMPAT_SOURCE_ID,
        source_alert_key="checkout-error",
        episode_started_at=NOW,
        alert_name="HighErrorRate",
        summary="错误率升高",
        description="checkout 错误率超过阈值",
        state="ACTIVE",
        severity="high",
        environment="production",
        service="checkout",
        entity_type="SERVICE",
        entity_key="a" * 64,
        entity_display_name="checkout",
        first_observed_at=NOW,
        last_observed_at=NOW,
        first_received_at=NOW,
        last_received_at=NOW,
        resolved_at=None,
        firing_observed=True,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
