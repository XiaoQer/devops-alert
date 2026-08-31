from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from incident_intelligence.domain.alert_sources import ALERTMANAGER_COMPAT_SOURCE_ID
from incident_intelligence.persistence.models import AlertLifecycleRow, IncidentRow
from incident_intelligence.persistence.session import make_session_factory
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.incident_rules import (
    CreateIncidentRuleCommand,
    IncidentRulePublishBlocked,
    IncidentRuleService,
    IncidentRuleVersionConflict,
)

NOW = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)


def _command() -> CreateIncidentRuleCommand:
    return CreateIncidentRuleCommand.model_validate(
        {
            "name": "支付链路异常",
            "description": "识别支付服务短时间内的多类告警",
            "config": {
                "environment": "production",
                "alert_source_ids": [],
                "services": ["checkout"],
                "group_by": "SERVICE",
                "window_minutes": 5,
                "conditions": [
                    {"type": "DISTINCT_ALERT_NAMES_GTE", "threshold": 2},
                ],
            },
        }
    )


@pytest.fixture
def service(migrated_engine: Engine) -> Iterator[IncidentRuleService]:
    session_factory = make_session_factory(migrated_engine)
    ids = iter(
        [
            "irl_11111111111111111111111111111111",
            "iro_22222222222222222222222222222222",
            "ird_33333333333333333333333333333333",
            "iro_44444444444444444444444444444444",
            "iro_55555555555555555555555555555555",
        ]
    )
    yield IncidentRuleService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        clock=lambda: NOW,
        id_factory=lambda prefix: next(ids),
    )


def _create(service: IncidentRuleService):
    return service.create(
        _command(),
        idempotency_key="create-1",
        actor="tester",
        request_id="req_create",
    ).rule


def test_publish_requires_current_successful_untruncated_dry_run(
    service: IncidentRuleService,
) -> None:
    rule = _create(service)

    with pytest.raises(IncidentRulePublishBlocked):
        service.publish(
            rule.id,
            expected_version=rule.version,
            idempotency_key="publish-1",
            actor="tester",
            request_id="req_publish",
        )


def test_dry_run_reads_real_alerts_without_writing_incident_or_alert(
    service: IncidentRuleService,
    migrated_engine: Engine,
) -> None:
    rule = _create(service)
    with Session(migrated_engine) as session:
        session.add_all(
            [
                _alert(1, "HighLatency", "medium"),
                _alert(2, "ErrorRate", "high"),
            ]
        )
        session.commit()
        versions_before = tuple(session.scalars(select(AlertLifecycleRow.version)))

    result = service.dry_run(
        rule.id,
        expected_version=rule.version,
        history_hours=6,
        actor="tester",
    )

    assert result.scanned_alert_count == 2
    assert result.match_count == 1
    assert result.truncated is False
    assert result.rule.publishable is True
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(IncidentRow)) == 0
        assert tuple(session.scalars(select(AlertLifecycleRow.version))) == versions_before


def test_update_rejects_stale_version(service: IncidentRuleService) -> None:
    rule = _create(service)

    with pytest.raises(IncidentRuleVersionConflict):
        service.update(
            rule.id,
            expected_version=99,
            name=rule.name,
            description=rule.description,
            config=rule.config,
            idempotency_key="update-stale",
            actor="tester",
            request_id="req_update",
        )


def _alert(index: int, name: str, severity: str) -> AlertLifecycleRow:
    received_at = NOW - timedelta(minutes=10 - index)
    return AlertLifecycleRow(
        id=f"alt_{index:032x}",
        alert_source_id=ALERTMANAGER_COMPAT_SOURCE_ID,
        source_alert_key=f"fingerprint-{index}",
        episode_started_at=received_at,
        alert_name=name,
        summary=name,
        description="测试告警",
        state="ACTIVE",
        severity=severity,
        environment="production",
        service="checkout",
        entity_type="SERVICE",
        entity_key="a" * 64,
        entity_display_name="checkout",
        first_observed_at=received_at,
        last_observed_at=received_at,
        first_received_at=received_at,
        last_received_at=received_at,
        resolved_at=None,
        firing_observed=True,
        version=1,
        created_at=received_at,
        updated_at=received_at,
    )
