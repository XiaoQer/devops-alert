from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import MetaData, Table, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from incident_intelligence.persistence import models as persistence_models
from incident_intelligence.persistence.base import Base

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def test_incident_operational_rows_are_registered_in_orm_metadata() -> None:
    assert persistence_models.OperationalIncidentRow.__tablename__ == "operational_incidents"
    assert {
        "operational_incidents",
        "operational_incident_alerts",
        "operational_incident_activities",
        "operational_incident_operations",
        "incident_evaluation_jobs",
        "incident_notification_routes",
        "incident_notification_route_operations",
        "incident_notification_outbox",
        "incident_feishu_threads",
        "feishu_event_receipts",
        "incident_reference_sequences",
    } <= set(Base.metadata.tables)


def test_incident_operations_schema_uses_new_tables_without_reusing_legacy_incidents(
    migrated_engine: Engine,
) -> None:
    inspector = inspect(migrated_engine)
    table_names = set(inspector.get_table_names())

    assert {
        "operational_incidents",
        "operational_incident_alerts",
        "operational_incident_activities",
        "operational_incident_operations",
        "incident_evaluation_jobs",
        "incident_notification_routes",
        "incident_notification_route_operations",
        "incident_notification_outbox",
        "incident_feishu_threads",
        "feishu_event_receipts",
        "incident_reference_sequences",
    } <= table_names
    assert "incidents" in table_names


def test_incident_schema_has_concurrency_and_idempotency_unique_constraints(
    migrated_engine: Engine,
) -> None:
    inspector = inspect(migrated_engine)

    assert "operational_incident_open_boundary" in _unique_names(inspector, "operational_incidents")
    assert inspector.get_pk_constraint("operational_incident_alerts")["constrained_columns"] == [
        "incident_id",
        "alert_id",
    ]
    assert "incident_evaluation_alert_version" in _unique_names(
        inspector, "incident_evaluation_jobs"
    )
    assert "incident_notification_key" in _unique_names(inspector, "incident_notification_outbox")
    assert "incident_route_enabled_environment" in _unique_names(
        inspector, "incident_notification_routes"
    )
    assert "operational_incident_operation_key" in _unique_names(
        inspector, "operational_incident_operations"
    )
    assert "incident_notification_route_operation_key" in _unique_names(
        inspector, "incident_notification_route_operations"
    )
    assert {
        "incident_feishu_thread_incident",
        "incident_feishu_thread_message",
    } <= _unique_names(inspector, "incident_feishu_threads")


def test_only_one_unresolved_incident_can_hold_the_same_open_boundary(
    migrated_engine: Engine,
) -> None:
    rules = Table("incident_rules", MetaData(), autoload_with=migrated_engine)
    incidents = Table("operational_incidents", MetaData(), autoload_with=migrated_engine)
    with Session(migrated_engine) as session:
        session.execute(rules.insert().values(**_rule_values()))
        session.execute(incidents.insert().values(**_incident_values()))
        session.commit()

        with pytest.raises((IntegrityError, OperationalError)):
            session.execute(
                incidents.insert().values(
                    **_incident_values(
                        id="inc_22222222222222222222222222222222",
                        reference="INC-20260901-002",
                    )
                )
            )
            session.commit()


def test_resolved_incidents_release_open_boundary_for_next_occurrence(
    migrated_engine: Engine,
) -> None:
    rules = Table("incident_rules", MetaData(), autoload_with=migrated_engine)
    incidents = Table("operational_incidents", MetaData(), autoload_with=migrated_engine)
    with Session(migrated_engine) as session:
        session.execute(rules.insert().values(**_rule_values()))
        session.execute(
            incidents.insert().values(
                **_incident_values(
                    state="RESOLVED",
                    open_boundary_key=None,
                    resolved_at=NOW,
                    resolution_summary="服务已恢复",
                )
            )
        )
        session.execute(
            incidents.insert().values(
                **_incident_values(
                    id="inc_22222222222222222222222222222222",
                    reference="INC-20260901-002",
                )
            )
        )
        session.commit()


def test_only_one_enabled_route_can_exist_per_environment(migrated_engine: Engine) -> None:
    routes = Table("incident_notification_routes", MetaData(), autoload_with=migrated_engine)
    with Session(migrated_engine) as session:
        session.execute(routes.insert().values(**_route_values()))
        session.commit()

        with pytest.raises((IntegrityError, OperationalError)):
            session.execute(
                routes.insert().values(
                    **_route_values(
                        id="inr_22222222222222222222222222222222",
                        chat_id="oc_second",
                    )
                )
            )
            session.commit()


def _unique_names(inspector, table_name: str) -> set[str]:
    return {
        item["name"]
        for item in inspector.get_unique_constraints(table_name)
        if item["name"] is not None
    }


def _rule_values() -> dict[str, object]:
    return {
        "id": "irl_11111111111111111111111111111111",
        "name": "支付链路异常",
        "description": "识别支付服务短时间内的多类告警",
        "state": "PUBLISHED",
        "environment": "production",
        "alert_source_ids": [],
        "services": ["checkout"],
        "group_by": "SERVICE",
        "window_minutes": 5,
        "conditions": [{"type": "DISTINCT_ALERT_NAMES_GTE", "threshold": 2}],
        "summary": "生产环境支付服务告警规则",
        "version": 2,
        "last_successful_dry_run_id": "ird_11111111111111111111111111111111",
        "last_successful_dry_run_version": 1,
        "last_successful_dry_run_at": NOW,
        "published_at": NOW,
        "disabled_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _incident_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": "inc_11111111111111111111111111111111",
        "reference": "INC-20260901-001",
        "title": "production checkout异常",
        "state": "OPEN",
        "severity": "high",
        "environment": "production",
        "group_by": "SERVICE",
        "group_key": "checkout",
        "group_display_name": "checkout",
        "incident_rule_id": "irl_11111111111111111111111111111111",
        "incident_rule_version": 2,
        "open_boundary_key": "a" * 64,
        "alert_count": 1,
        "active_alert_count": 1,
        "distinct_alert_name_count": 1,
        "version": 1,
        "opened_at": NOW,
        "acknowledged_at": None,
        "resolved_at": None,
        "resolution_summary": None,
        "last_alert_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return values


def _route_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": "inr_11111111111111111111111111111111",
        "environment": "production",
        "chat_id": "oc_primary",
        "chat_name": "生产事故群",
        "enabled": True,
        "enabled_environment_key": "production",
        "version": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return values
