from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from incident_intelligence.persistence import models as persistence_models
from incident_intelligence.persistence.base import Base


def test_monitoring_evidence_rows_are_registered_in_orm_metadata() -> None:
    assert persistence_models.EvidenceRunRow.__tablename__ == "incident_evidence_runs"
    assert {
        "monitoring_data_sources",
        "incident_evidence_runs",
        "incident_evidence_items",
        "evidence_collection_tasks",
        "evidence_collection_operations",
    } <= set(Base.metadata.tables)


def test_schema_has_evidence_tables_and_concurrency_constraints(
    migrated_engine: Engine,
) -> None:
    inspector = inspect(migrated_engine)
    assert {
        "monitoring_data_sources",
        "incident_evidence_runs",
        "incident_evidence_items",
        "evidence_collection_tasks",
        "evidence_collection_operations",
    } <= set(inspector.get_table_names())

    assert "monitoring_source_enabled" in _unique_names(inspector, "monitoring_data_sources")
    assert "incident_auto_evidence_run" in _unique_names(inspector, "incident_evidence_runs")
    assert "evidence_item_key" in _unique_names(inspector, "incident_evidence_items")
    assert "evidence_operation_key" in _unique_names(inspector, "evidence_collection_operations")


def _unique_names(inspector, table_name: str) -> set[str]:
    return {
        item["name"]
        for item in inspector.get_unique_constraints(table_name)
        if item["name"] is not None
    }
