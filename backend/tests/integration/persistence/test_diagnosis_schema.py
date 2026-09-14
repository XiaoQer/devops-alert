from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from incident_intelligence.persistence import models as persistence_models
from incident_intelligence.persistence.base import Base


def test_controlled_diagnosis_rows_are_registered_in_orm_metadata() -> None:
    assert persistence_models.IncidentDiagnosisRunRow.__tablename__ == "incident_diagnosis_runs"
    assert {
        "incident_diagnosis_runs",
        "incident_diagnosis_snapshots",
        "incident_diagnosis_tasks",
        "incident_diagnosis_tool_receipts",
        "incident_diagnosis_reports",
        "incident_diagnosis_operations",
    } <= set(Base.metadata.tables)


def test_schema_has_diagnosis_tables_and_active_run_constraint(
    migrated_engine: Engine,
) -> None:
    inspector = inspect(migrated_engine)
    assert {
        "incident_diagnosis_runs",
        "incident_diagnosis_snapshots",
        "incident_diagnosis_tasks",
        "incident_diagnosis_tool_receipts",
        "incident_diagnosis_reports",
        "incident_diagnosis_operations",
    } <= set(inspector.get_table_names())
    assert "incident_active_diagnosis_run" in _unique_names(
        inspector,
        "incident_diagnosis_runs",
    )
    assert "diagnosis_task_run" in _unique_names(inspector, "incident_diagnosis_tasks")
    assert "diagnosis_tool_receipt_key" in _unique_names(
        inspector,
        "incident_diagnosis_tool_receipts",
    )
    assert "diagnosis_operation_key" in _unique_names(
        inspector,
        "incident_diagnosis_operations",
    )


def test_queued_run_requires_the_active_slot(migrated_engine: Engine) -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    _seed_parent_rows(migrated_engine, now=now)
    with Session(migrated_engine) as session:
        session.add(
            persistence_models.IncidentDiagnosisRunRow(
                id="drun_11111111111111111111111111111111",
                incident_id="inc_11111111111111111111111111111111",
                evidence_run_id="evr_22222222222222222222222222222222",
                state="QUEUED",
                active_slot=None,
                requested_by="operator",
                created_at=now,
                started_at=None,
                completed_at=None,
                version=1,
            )
        )
        with pytest.raises((IntegrityError, OperationalError)):
            session.commit()


def _seed_parent_rows(engine: Engine, *, now: datetime) -> None:
    from sqlalchemy import MetaData, Table

    with Session(engine) as session:
        metadata = MetaData()
        rules = Table("incident_rules", metadata, autoload_with=session.bind)
        incidents = Table("operational_incidents", metadata, autoload_with=session.bind)
        evidence_runs = Table("incident_evidence_runs", metadata, autoload_with=session.bind)
        session.execute(
            rules.insert().values(
                id="irl_33333333333333333333333333333333",
                name="诊断约束测试规则",
                description="测试",
                state="PUBLISHED",
                environment="testing",
                alert_source_ids=[],
                services=["checkout"],
                group_by="SERVICE",
                window_minutes=5,
                conditions=[{"type": "ACTIVE_ALERTS_GTE", "threshold": 1}],
                summary="测试",
                version=1,
                last_successful_dry_run_id="ird_44444444444444444444444444444444",
                last_successful_dry_run_version=1,
                last_successful_dry_run_at=now,
                published_at=now,
                disabled_at=None,
                created_at=now,
                updated_at=now,
            )
        )
        session.execute(
            incidents.insert().values(
                id="inc_11111111111111111111111111111111",
                reference="INC-20260914-001",
                title="testing checkout异常",
                state="OPEN",
                severity="high",
                environment="testing",
                group_by="SERVICE",
                group_key="checkout",
                group_display_name="checkout",
                incident_rule_id="irl_33333333333333333333333333333333",
                incident_rule_version=1,
                open_boundary_key="a" * 64,
                alert_count=1,
                active_alert_count=1,
                distinct_alert_name_count=1,
                version=1,
                opened_at=now,
                acknowledged_at=None,
                resolved_at=None,
                resolution_summary=None,
                last_alert_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.execute(
            evidence_runs.insert().values(
                id="evr_22222222222222222222222222222222",
                incident_id="inc_11111111111111111111111111111111",
                trigger_kind="AUTOMATIC",
                automatic_slot=1,
                state="SUCCEEDED",
                anchor_at=now,
                baseline_start=now,
                baseline_end=now,
                fault_start=now,
                fault_end=now,
                environment="testing",
                service_name="checkout",
                alert_names=["HighErrorRate"],
                context_facts={},
                package_versions={},
                succeeded_count=0,
                skipped_count=0,
                missing_count=0,
                failed_count=0,
                failure_summary=None,
                requested_by="test",
                created_at=now,
                started_at=None,
                completed_at=now,
                version=1,
            )
        )
        session.commit()


def _unique_names(inspector, table_name: str) -> set[str]:
    return {
        item["name"]
        for item in inspector.get_unique_constraints(table_name)
        if item["name"] is not None
    }
