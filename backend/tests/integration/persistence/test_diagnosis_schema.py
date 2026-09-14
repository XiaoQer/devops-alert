from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

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


def _unique_names(inspector, table_name: str) -> set[str]:
    return {
        item["name"]
        for item in inspector.get_unique_constraints(table_name)
        if item["name"] is not None
    }
