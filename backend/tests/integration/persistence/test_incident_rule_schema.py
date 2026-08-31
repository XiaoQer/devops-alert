from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import MetaData, Table, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)


def _rule_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": "irl_11111111111111111111111111111111",
        "name": "支付链路异常",
        "description": "识别支付服务短时间内的多类告警",
        "state": "DRAFT",
        "environment": "production",
        "alert_source_ids": [],
        "services": ["checkout"],
        "group_by": "SERVICE",
        "window_minutes": 5,
        "conditions": [{"type": "DISTINCT_ALERT_NAMES_GTE", "threshold": 2}],
        "summary": "生产环境支付服务告警规则",
        "version": 1,
        "last_successful_dry_run_id": None,
        "last_successful_dry_run_version": None,
        "last_successful_dry_run_at": None,
        "published_at": None,
        "disabled_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return values


def test_incident_rule_schema_contains_rules_dry_runs_and_operations(
    migrated_engine: Engine,
) -> None:
    inspector = inspect(migrated_engine)

    assert {
        "incident_rules",
        "incident_rule_dry_runs",
        "incident_rule_operations",
    } <= set(inspector.get_table_names())
    assert {
        "state",
        "environment",
        "group_by",
        "window_minutes",
        "conditions",
        "version",
        "last_successful_dry_run_version",
    } <= {column["name"] for column in inspector.get_columns("incident_rules")}
    assert "rule_name" in {
        item["name"] for item in inspector.get_unique_constraints("incident_rules")
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "UNKNOWN"},
        {"window_minutes": 0},
        {"window_minutes": 61},
        {"version": 0},
        {"state": "PUBLISHED", "published_at": None},
    ],
)
def test_incident_rule_schema_rejects_invalid_state_and_boundaries(
    changes: dict[str, object],
    migrated_engine: Engine,
) -> None:
    table = Table("incident_rules", MetaData(), autoload_with=migrated_engine)
    with Session(migrated_engine) as session, pytest.raises((IntegrityError, OperationalError)):
        session.execute(table.insert().values(**_rule_values(**changes)))
        session.commit()


def test_rule_name_is_unique(migrated_engine: Engine) -> None:
    table = Table("incident_rules", MetaData(), autoload_with=migrated_engine)
    with Session(migrated_engine) as session:
        session.execute(table.insert().values(**_rule_values()))
        session.commit()
        with pytest.raises((IntegrityError, OperationalError)):
            session.execute(
                table.insert().values(**_rule_values(id="irl_22222222222222222222222222222222"))
            )
            session.commit()
