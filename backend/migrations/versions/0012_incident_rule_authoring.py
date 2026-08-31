"""建立手动 Incident 规则、历史试运行和操作审计。

Revision ID: 0012_incident_rule_authoring
Revises: 0011_alert_lifecycle_projection
Create Date: 2026-08-31
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0012_incident_rule_authoring"
down_revision: str | None = "0011_alert_lifecycle_projection"
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
    op.create_table(
        "incident_rules",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(1_000), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("alert_source_ids", mysql.JSON(), nullable=False),
        sa.Column("services", mysql.JSON(), nullable=False),
        sa.Column("group_by", sa.String(16), nullable=False),
        sa.Column("window_minutes", sa.Integer(), nullable=False),
        sa.Column("conditions", mysql.JSON(), nullable=False),
        sa.Column("summary", sa.String(2_000), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("last_successful_dry_run_id", sa.String(36), nullable=True),
        sa.Column("last_successful_dry_run_version", sa.Integer(), nullable=True),
        sa.Column("last_successful_dry_run_at", utc_datetime(), nullable=True),
        sa.Column("published_at", utc_datetime(), nullable=True),
        sa.Column("disabled_at", utc_datetime(), nullable=True),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("updated_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('DRAFT', 'PUBLISHED', 'DISABLED')",
            name=op.f("ck_incident_rules_state"),
        ),
        sa.CheckConstraint(
            "environment REGEXP '^[a-z][a-z0-9-]{0,31}$'",
            name=op.f("ck_incident_rules_environment"),
        ),
        sa.CheckConstraint(
            "group_by IN ('SERVICE', 'ENTITY')",
            name=op.f("ck_incident_rules_group_by"),
        ),
        sa.CheckConstraint(
            "window_minutes BETWEEN 1 AND 60",
            name=op.f("ck_incident_rules_window_minutes"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_incident_rules_version")),
        sa.CheckConstraint(
            "JSON_TYPE(alert_source_ids) = 'ARRAY' AND JSON_LENGTH(alert_source_ids) <= 50",
            name=op.f("ck_incident_rules_source_ids"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(services) = 'ARRAY' AND JSON_LENGTH(services) <= 50",
            name=op.f("ck_incident_rules_services"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(conditions) = 'ARRAY' AND JSON_LENGTH(conditions) BETWEEN 1 AND 4",
            name=op.f("ck_incident_rules_conditions"),
        ),
        sa.CheckConstraint(
            "(last_successful_dry_run_id IS NULL "
            "AND last_successful_dry_run_version IS NULL "
            "AND last_successful_dry_run_at IS NULL) OR "
            "(last_successful_dry_run_id IS NOT NULL "
            "AND last_successful_dry_run_version BETWEEN 1 AND version "
            "AND last_successful_dry_run_at IS NOT NULL)",
            name=op.f("ck_incident_rules_dry_run_pair"),
        ),
        sa.CheckConstraint(
            "(state = 'DRAFT' AND published_at IS NULL AND disabled_at IS NULL) OR "
            "(state = 'PUBLISHED' AND published_at IS NOT NULL AND disabled_at IS NULL) OR "
            "(state = 'DISABLED' AND published_at IS NOT NULL AND disabled_at IS NOT NULL)",
            name=op.f("ck_incident_rules_state_times"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_rules")),
        sa.UniqueConstraint("name", name="rule_name"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_incident_rules_state", "incident_rules", ["state"])
    op.create_index(
        "ix_incident_rules_updated_at",
        "incident_rules",
        ["updated_at", "id"],
    )

    op.create_table(
        "incident_rule_dry_runs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("rule_id", sa.String(36), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("history_hours", sa.Integer(), nullable=False),
        sa.Column("scanned_alert_count", sa.Integer(), nullable=False),
        sa.Column("match_count", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("matches", mysql.JSON(), nullable=False),
        sa.Column("executed_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "rule_version >= 1",
            name=op.f("ck_incident_rule_dry_runs_rule_version"),
        ),
        sa.CheckConstraint(
            "history_hours IN (1, 6, 12, 24, 48)",
            name=op.f("ck_incident_rule_dry_runs_history_hours"),
        ),
        sa.CheckConstraint(
            "scanned_alert_count BETWEEN 0 AND 10001 AND match_count BETWEEN 0 AND 100",
            name=op.f("ck_incident_rule_dry_runs_counts"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(matches) = 'ARRAY' AND JSON_LENGTH(matches) <= 100",
            name=op.f("ck_incident_rule_dry_runs_matches"),
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["incident_rules.id"],
            name="fk_incident_rule_dry_runs_rule_id_incident_rules",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_rule_dry_runs")),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_incident_rule_dry_runs_rule_time",
        "incident_rule_dry_runs",
        ["rule_id", "executed_at", "id"],
    )

    op.create_table(
        "incident_rule_operations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("scope", sa.String(96), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("command_fingerprint", sa.String(64), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("rule_id", sa.String(36), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("completed_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "action IN ('CREATE', 'UPDATE', 'DELETE', 'PUBLISH', 'DISABLE', 'COPY')",
            name=op.f("ck_incident_rule_operations_action"),
        ),
        sa.CheckConstraint(
            "char_length(idempotency_key_hash) = 64 AND char_length(command_fingerprint) = 64",
            name=op.f("ck_incident_rule_operations_hashes"),
        ),
        sa.CheckConstraint(
            "result_version >= 1",
            name=op.f("ck_incident_rule_operations_result_version"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_rule_operations")),
        sa.UniqueConstraint(
            "scope",
            "idempotency_key_hash",
            name="incident_rule_operation_key",
        ),
        **TABLE_OPTIONS,
    )


def downgrade() -> None:
    op.drop_table("incident_rule_operations")
    op.drop_table("incident_rule_dry_runs")
    op.drop_table("incident_rules")
