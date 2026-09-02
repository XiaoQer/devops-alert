"""建立 Incident 监控取证运行、证据和异步任务表。

Revision ID: 0019_monitoring_evidence
Revises: 0018_restore_system_sources
Create Date: 2026-09-02
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0019_monitoring_evidence"
down_revision: str | None = "0018_restore_system_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_OPTIONS: dict[str, Any] = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_bin",
}


def utc_datetime() -> mysql.DATETIME:
    return mysql.DATETIME(fsp=6)


def upgrade() -> None:
    _extend_incident_activity_kinds(include_evidence=True)
    _create_monitoring_data_sources()
    _create_evidence_runs()
    _create_evidence_items()
    _create_evidence_tasks()
    _create_evidence_operations()


def downgrade() -> None:
    op.drop_table("evidence_collection_operations")
    op.drop_index("ix_evidence_collection_tasks_claim", table_name="evidence_collection_tasks")
    op.drop_table("evidence_collection_tasks")
    op.drop_index("ix_incident_evidence_items_run", table_name="incident_evidence_items")
    op.drop_table("incident_evidence_items")
    op.drop_index("ix_incident_evidence_runs_state", table_name="incident_evidence_runs")
    op.drop_index("ix_incident_evidence_runs_incident", table_name="incident_evidence_runs")
    op.drop_table("incident_evidence_runs")
    op.drop_index("ix_monitoring_data_sources_environment", table_name="monitoring_data_sources")
    op.drop_table("monitoring_data_sources")
    op.execute(
        sa.text(
            "DELETE FROM operational_incident_activities "
            "WHERE kind IN ('EVIDENCE_COLLECTION_COMPLETED', "
            "'EVIDENCE_COLLECTION_PARTIAL', 'EVIDENCE_COLLECTION_FAILED')"
        )
    )
    _extend_incident_activity_kinds(include_evidence=False)


def _extend_incident_activity_kinds(*, include_evidence: bool) -> None:
    op.execute(
        sa.text(
            "ALTER TABLE operational_incident_activities "
            "DROP CHECK ck_operational_incident_activities_kind"
        )
    )
    values = (
        "'INCIDENT_CREATED', 'ALERTS_LINKED', 'SEVERITY_ESCALATED', "
        "'ALL_ALERTS_RECOVERED', 'ACKNOWLEDGED', 'RESOLVED', "
        "'FEISHU_MESSAGE_RECORDED', 'NOTIFICATION_FAILED'"
    )
    if include_evidence:
        values += (
            ", 'EVIDENCE_COLLECTION_COMPLETED', 'EVIDENCE_COLLECTION_PARTIAL', "
            "'EVIDENCE_COLLECTION_FAILED'"
        )
    op.execute(
        sa.text(
            "ALTER TABLE operational_incident_activities "
            "ADD CONSTRAINT ck_operational_incident_activities_kind "
            f"CHECK (kind IN ({values}))"
        )
    )


def _create_monitoring_data_sources() -> None:
    op.create_table(
        "monitoring_data_sources",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("base_url", sa.String(2_000), nullable=False),
        sa.Column("credential_env_key", sa.String(128), nullable=True),
        sa.Column("field_mapping", mysql.JSON(), nullable=False),
        sa.Column("verify_tls", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("enabled_slot", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("last_test_state", sa.String(16), nullable=True),
        sa.Column("last_test_latency_ms", sa.Integer(), nullable=True),
        sa.Column("last_compatible_version", sa.String(64), nullable=True),
        sa.Column("last_test_error_code", sa.String(64), nullable=True),
        sa.Column("last_tested_at", utc_datetime(), nullable=True),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("updated_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "environment REGEXP '^[a-z][a-z0-9-]{0,31}$'",
            name=op.f("ck_monitoring_data_sources_environment"),
        ),
        sa.CheckConstraint(
            "source_type IN ('PROMETHEUS', 'ELASTICSEARCH', 'SKYWALKING')",
            name=op.f("ck_monitoring_data_sources_source_type"),
        ),
        sa.CheckConstraint(
            "(enabled = 1 AND enabled_slot = 1) OR (enabled = 0 AND enabled_slot IS NULL)",
            name=op.f("ck_monitoring_data_sources_enabled_slot"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_monitoring_data_sources_version")),
        sa.PrimaryKeyConstraint("id", name="pk_monitoring_data_sources"),
        sa.UniqueConstraint(
            "environment", "source_type", "enabled_slot", name="monitoring_source_enabled"
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_monitoring_data_sources_environment",
        "monitoring_data_sources",
        ["environment", "source_type"],
    )


def _create_evidence_runs() -> None:
    op.create_table(
        "incident_evidence_runs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("incident_id", sa.String(36), nullable=False),
        sa.Column("trigger_kind", sa.String(16), nullable=False),
        sa.Column("automatic_slot", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("anchor_at", utc_datetime(), nullable=False),
        sa.Column("baseline_start", utc_datetime(), nullable=False),
        sa.Column("baseline_end", utc_datetime(), nullable=False),
        sa.Column("fault_start", utc_datetime(), nullable=False),
        sa.Column("fault_end", utc_datetime(), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("service_name", sa.String(128), nullable=True),
        sa.Column("alert_names", mysql.JSON(), nullable=False),
        sa.Column("context_facts", mysql.JSON(), nullable=False),
        sa.Column("package_versions", mysql.JSON(), nullable=False),
        sa.Column("succeeded_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("missing_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("failure_summary", sa.String(1_000), nullable=True),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("started_at", utc_datetime(), nullable=True),
        sa.Column("completed_at", utc_datetime(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "state IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED')",
            name=op.f("ck_incident_evidence_runs_state"),
        ),
        sa.CheckConstraint(
            "trigger_kind IN ('AUTOMATIC', 'MANUAL')",
            name=op.f("ck_incident_evidence_runs_trigger_kind"),
        ),
        sa.CheckConstraint(
            "(trigger_kind = 'AUTOMATIC' AND automatic_slot = 1) OR "
            "(trigger_kind = 'MANUAL' AND automatic_slot IS NULL)",
            name=op.f("ck_incident_evidence_runs_automatic_slot"),
        ),
        sa.CheckConstraint(
            "environment REGEXP '^[a-z][a-z0-9-]{0,31}$'",
            name=op.f("ck_incident_evidence_runs_environment"),
        ),
        sa.CheckConstraint(
            "succeeded_count >= 0 AND skipped_count >= 0 AND missing_count >= 0 "
            "AND failed_count >= 0 AND version >= 1",
            name=op.f("ck_incident_evidence_runs_counts"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["operational_incidents.id"],
            name="fk_incident_evidence_runs_incident",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_incident_evidence_runs"),
        sa.UniqueConstraint("incident_id", "automatic_slot", name="incident_auto_evidence_run"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_incident_evidence_runs_incident",
        "incident_evidence_runs",
        ["incident_id", "created_at", "id"],
    )
    op.create_index(
        "ix_incident_evidence_runs_state",
        "incident_evidence_runs",
        ["state", "created_at"],
    )


def _create_evidence_items() -> None:
    op.create_table(
        "incident_evidence_items",
        sa.Column("id", sa.String(39), nullable=False),
        sa.Column("evidence_run_id", sa.String(36), nullable=False),
        sa.Column("evidence_key", sa.String(160), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("package_id", sa.String(128), nullable=False),
        sa.Column("package_version", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.String(128), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("evidence_type", sa.String(32), nullable=False),
        sa.Column("query_started_at", utc_datetime(), nullable=False),
        sa.Column("query_ended_at", utc_datetime(), nullable=False),
        sa.Column("step_seconds", sa.Integer(), nullable=True),
        sa.Column("query_parameters", mysql.JSON(), nullable=False),
        sa.Column("baseline_summary", mysql.JSON(), nullable=False),
        sa.Column("fault_summary", mysql.JSON(), nullable=False),
        sa.Column("interpretation", sa.String(2_000), nullable=True),
        sa.Column("normalized_result", mysql.JSON(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('PROMETHEUS', 'ELASTICSEARCH', 'SKYWALKING', 'PLATFORM')",
            name=op.f("ck_incident_evidence_items_source_type"),
        ),
        sa.CheckConstraint(
            "state IN ('SUCCEEDED', 'NO_DATA', 'INSUFFICIENT_BASELINE', "
            "'MISSING_TARGET', 'SKIPPED_DEPENDENCY', 'FAILED')",
            name=op.f("ck_incident_evidence_items_state"),
        ),
        sa.CheckConstraint(
            "evidence_type IN ('METRIC_TIMESERIES', 'METRIC_COMPARISON', "
            "'LOG_AGGREGATION', 'LOG_SAMPLE', 'ENDPOINT_RANKING', "
            "'DEPENDENCY_RANKING', 'TRACE_SUMMARY', 'CROSS_SOURCE_CORRELATION')",
            name=op.f("ck_incident_evidence_items_evidence_type"),
        ),
        sa.CheckConstraint(
            "package_version >= 1 AND template_version >= 1",
            name=op.f("ck_incident_evidence_items_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["evidence_run_id"],
            ["incident_evidence_runs.id"],
            name="fk_incident_evidence_items_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_incident_evidence_items"),
        sa.UniqueConstraint("evidence_run_id", "evidence_key", name="evidence_item_key"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_incident_evidence_items_run",
        "incident_evidence_items",
        ["evidence_run_id", "created_at", "id"],
    )


def _create_evidence_tasks() -> None:
    op.create_table(
        "evidence_collection_tasks",
        sa.Column("id", sa.String(39), nullable=False),
        sa.Column("evidence_run_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", utc_datetime(), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_until", utc_datetime(), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("completed_at", utc_datetime(), nullable=True),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("updated_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('PENDING', 'LEASED', 'SUCCEEDED', 'FAILED')",
            name=op.f("ck_evidence_collection_tasks_state"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 5",
            name=op.f("ck_evidence_collection_tasks_attempts"),
        ),
        sa.CheckConstraint(
            "(state = 'LEASED' AND lease_owner IS NOT NULL AND lease_until IS NOT NULL) "
            "OR (state <> 'LEASED' AND lease_owner IS NULL AND lease_until IS NULL)",
            name=op.f("ck_evidence_collection_tasks_lease"),
        ),
        sa.ForeignKeyConstraint(
            ["evidence_run_id"],
            ["incident_evidence_runs.id"],
            name="fk_evidence_collection_tasks_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_collection_tasks"),
        sa.UniqueConstraint("evidence_run_id", name="evidence_task_run"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_evidence_collection_tasks_claim",
        "evidence_collection_tasks",
        ["state", "next_attempt_at", "created_at"],
    )


def _create_evidence_operations() -> None:
    op.create_table(
        "evidence_collection_operations",
        sa.Column("id", sa.String(37), nullable=False),
        sa.Column("scope", sa.String(96), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("command_fingerprint", sa.String(64), nullable=False),
        sa.Column("evidence_run_id", sa.String(36), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("completed_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "char_length(idempotency_key_hash) = 64 AND char_length(command_fingerprint) = 64",
            name=op.f("ck_evidence_collection_operations_hashes"),
        ),
        sa.ForeignKeyConstraint(
            ["evidence_run_id"],
            ["incident_evidence_runs.id"],
            name="fk_evidence_collection_operations_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_collection_operations"),
        sa.UniqueConstraint("scope", "idempotency_key_hash", name="evidence_operation_key"),
        **TABLE_OPTIONS,
    )
