from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from incident_intelligence.domain.alert_sources import ALERTMANAGER_COMPAT_SOURCE_ID
from incident_intelligence.domain.incident_rules import create_rule, update_draft
from incident_intelligence.persistence.alert_center_repository import AlertRepository
from incident_intelligence.persistence.incident_rule_repository import (
    IncidentRuleDryRunRecord,
    IncidentRuleOperationRecord,
    IncidentRuleRepository,
)
from incident_intelligence.persistence.models import AlertLifecycleRow, IncidentRuleDryRunRow

NOW = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)


def _rule():
    from incident_intelligence.domain.incident_rules import IncidentRuleConfig

    return create_rule(
        rule_id="irl_11111111111111111111111111111111",
        name="支付链路异常",
        description="识别支付服务短时间内的多类告警",
        config=IncidentRuleConfig.model_validate(
            {
                "environment": "production",
                "alert_source_ids": (),
                "services": ("checkout",),
                "group_by": "SERVICE",
                "window_minutes": 5,
                "conditions": ({"type": "DISTINCT_ALERT_NAMES_GTE", "threshold": 2},),
            }
        ),
        now=NOW,
    )


def _alert(index: int, *, service: str = "checkout") -> AlertLifecycleRow:
    received_at = NOW + timedelta(minutes=index)
    return AlertLifecycleRow(
        id=f"alt_{index:032x}",
        alert_source_id=ALERTMANAGER_COMPAT_SOURCE_ID,
        source_alert_key=f"fingerprint-{index}",
        episode_started_at=received_at,
        alert_name=f"Alert{index}",
        summary=f"告警 {index}",
        description="测试告警",
        state="ACTIVE",
        severity="high",
        environment="production",
        service=service,
        entity_type="SERVICE",
        entity_key=f"{index:x}"[-1] * 64,
        entity_display_name=service,
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


def test_repository_round_trips_rule_and_lists_newest_first(migrated_engine: Engine) -> None:
    rule = _rule()
    with Session(migrated_engine) as session:
        repository = IncidentRuleRepository(session)
        repository.insert(rule)
        session.commit()

        loaded = repository.get(rule.id)
        page = repository.list(state="DRAFT", limit=10, offset=0)

        assert loaded == rule
        assert page.items == (rule,)
        assert page.total == 1


def test_repository_update_uses_expected_version(migrated_engine: Engine) -> None:
    original = _rule()
    changed = update_draft(
        original,
        name="支付链路持续异常",
        description=original.description,
        config=original.config,
        now=NOW + timedelta(minutes=1),
    )
    with Session(migrated_engine) as session:
        repository = IncidentRuleRepository(session)
        repository.insert(original)
        session.commit()

        assert repository.update(changed, expected_version=1) is True
        session.commit()
        assert (
            repository.update(
                changed.model_copy(update={"version": 3}),
                expected_version=1,
            )
            is False
        )


def test_evaluation_query_is_bounded_ordered_and_filtered(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        session.add_all([_alert(3), _alert(1), _alert(2), _alert(4, service="catalog")])
        session.commit()

        rows = AlertRepository(session).list_for_rule_evaluation(
            environment="production",
            alert_source_ids=(),
            services=("checkout",),
            received_from=NOW,
            received_to=NOW + timedelta(hours=1),
            limit=2,
        )

        assert [row.alert_name for row in rows] == ["Alert1", "Alert2"]
        assert all(row.service == "checkout" for row in rows)


def test_repository_persists_bounded_dry_run_summary(migrated_engine: Engine) -> None:
    rule = _rule()
    with Session(migrated_engine) as session:
        repository = IncidentRuleRepository(session)
        repository.insert(rule)
        repository.insert_dry_run(
            IncidentRuleDryRunRecord(
                id="ird_22222222222222222222222222222222",
                rule_id=rule.id,
                rule_version=rule.version,
                history_hours=6,
                scanned_alert_count=3,
                match_count=1,
                truncated=False,
                matches=({"group_key": "checkout", "example_alert_ids": ["alt_1"]},),
                executed_at=NOW,
            )
        )
        session.commit()

        row = session.scalar(select(IncidentRuleDryRunRow))
        assert row is not None
        assert row.matches == [{"group_key": "checkout", "example_alert_ids": ["alt_1"]}]


def test_repository_finds_idempotent_operation_and_deletes_only_matching_draft(
    migrated_engine: Engine,
) -> None:
    rule = _rule()
    operation = IncidentRuleOperationRecord(
        id="iro_33333333333333333333333333333333",
        scope="incident-rule:create",
        idempotency_key_hash="4" * 64,
        command_fingerprint="5" * 64,
        action="CREATE",
        rule_id=rule.id,
        result_version=rule.version,
        actor="tester",
        request_id="req_1",
        summary="创建 Incident 规则",
        completed_at=NOW,
    )
    with Session(migrated_engine) as session:
        repository = IncidentRuleRepository(session)
        repository.insert(rule)
        repository.insert_operation(operation)
        session.commit()

        assert (
            repository.find_operation(
                scope=operation.scope,
                idempotency_key_hash=operation.idempotency_key_hash,
            )
            == operation
        )
        assert repository.delete_draft(rule.id, expected_version=2) is False
        assert repository.delete_draft(rule.id, expected_version=1) is True
        assert session.scalar(select(func.count()).select_from(IncidentRuleDryRunRow)) == 0
