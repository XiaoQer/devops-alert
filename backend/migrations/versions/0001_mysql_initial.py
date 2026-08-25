"""建立 MySQL 事故领域初始表。

Revision ID: 0001_mysql_initial
Revises:
Create Date: 2026-08-25
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0001_mysql_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEVERITIES = "'critical', 'high', 'medium', 'low'"
ENVIRONMENTS = "'production', 'staging', 'development', 'unknown'"
EVENT_TYPES = "'manual.reported', 'alert.firing', 'alert.resolved'"
PROJECTION_OUTCOMES = "'opened', 'updated', 'resolved', 'reopened', 'stale', 'orphan_resolved'"
TABLE_OPTIONS: dict[str, Any] = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_bin",
}


def utc_datetime() -> mysql.DATETIME:
    return mysql.DATETIME(fsp=6)


def upgrade() -> None:
    op.create_table(
        "signal_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_event_id", sa.String(256), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.String(2_000), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("service", sa.String(128), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("observed_at", utc_datetime(), nullable=False),
        sa.Column("received_at", utc_datetime(), nullable=False),
        sa.Column("facts", mysql.JSON(), nullable=False),
        sa.Column("payload_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            f"severity IN ({SEVERITIES})",
            name=op.f("ck_signal_events_signal_severity"),
        ),
        sa.CheckConstraint(
            f"environment IN ({ENVIRONMENTS})",
            name=op.f("ck_signal_events_signal_environment"),
        ),
        sa.CheckConstraint(
            f"event_type IN ({EVENT_TYPES})",
            name=op.f("ck_signal_events_signal_event_type"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signal_events")),
        sa.UniqueConstraint("source", "source_event_id", name="source_identity"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_signal_events_observed_at", "signal_events", ["observed_at"])

    op.create_table(
        "alerts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("signal_event_id", sa.String(36), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_instance", sa.String(64), nullable=False),
        sa.Column("source_alert_key", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("service", sa.String(128), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("first_observed_at", utc_datetime(), nullable=False),
        sa.Column("last_observed_at", utc_datetime(), nullable=False),
        sa.Column("state_changed_at", utc_datetime(), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "state IN ('ACTIVE', 'RESOLVED', 'SUPPRESSED')",
            name=op.f("ck_alerts_alert_state"),
        ),
        sa.CheckConstraint(
            f"severity IN ({SEVERITIES})",
            name=op.f("ck_alerts_alert_severity"),
        ),
        sa.CheckConstraint(
            f"environment IN ({ENVIRONMENTS})",
            name=op.f("ck_alerts_alert_environment"),
        ),
        sa.CheckConstraint(
            "char_length(source_instance) = 64",
            name=op.f("ck_alerts_alert_source_instance"),
        ),
        sa.CheckConstraint(
            "char_length(source_alert_key) >= 1",
            name=op.f("ck_alerts_alert_source_alert_key"),
        ),
        sa.ForeignKeyConstraint(
            ["signal_event_id"],
            ["signal_events.id"],
            name=op.f("fk_alerts_signal_event_id_signal_events"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
        sa.UniqueConstraint(
            "source", "source_instance", "source_alert_key", name="alert_source_identity"
        ),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_alerts_state", "alerts", ["state"])
    op.create_index("ix_alerts_signal_event_id", "alerts", ["signal_event_id"])

    op.create_table(
        "incidents",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("primary_alert_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("service", sa.String(128), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("detected_at", utc_datetime(), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "state IN ('DETECTED', 'TRIAGING', 'INVESTIGATING', 'MITIGATING', "
            "'MONITORING_RECOVERY', 'RESOLVED', 'CLOSED')",
            name=op.f("ck_incidents_incident_state"),
        ),
        sa.CheckConstraint(
            f"severity IN ({SEVERITIES})",
            name=op.f("ck_incidents_incident_severity"),
        ),
        sa.CheckConstraint(
            f"environment IN ({ENVIRONMENTS})",
            name=op.f("ck_incidents_incident_environment"),
        ),
        sa.ForeignKeyConstraint(
            ["primary_alert_id"],
            ["alerts.id"],
            name=op.f("fk_incidents_primary_alert_id_alerts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incidents")),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_incidents_state", "incidents", ["state"])
    op.create_index("ix_incidents_primary_alert_id", "incidents", ["primary_alert_id"])

    op.create_table(
        "diagnosis_runs",
        sa.Column("id", sa.String(37), nullable=False),
        sa.Column("incident_id", sa.String(36), nullable=False),
        sa.Column("incident_context_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "state IN ('QUEUED', 'COLLECTING', 'NORMALIZING', 'SNAPSHOT_READY', "
            "'ANALYZING', 'REPORT_READY', 'PARTIAL', 'FAILED', 'REVIEW_REQUIRED')",
            name=op.f("ck_diagnosis_runs_diagnosis_state"),
        ),
        sa.CheckConstraint(
            "incident_context_version >= 1",
            name=op.f("ck_diagnosis_runs_diagnosis_context_version"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_diagnosis_runs_incident_id_incidents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_diagnosis_runs")),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_diagnosis_runs_state", "diagnosis_runs", ["state"])
    op.create_index("ix_diagnosis_runs_incident_id", "diagnosis_runs", ["incident_id"])

    op.create_table(
        "ingestion_keys",
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("payload_fingerprint", sa.String(64), nullable=False),
        sa.Column("signal_event_id", sa.String(36), nullable=False),
        sa.Column("alert_id", sa.String(36), nullable=False),
        sa.Column("incident_id", sa.String(36), nullable=False),
        sa.Column("diagnosis_run_id", sa.String(37), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.PrimaryKeyConstraint("scope", "idempotency_key", name=op.f("pk_ingestion_keys")),
        **TABLE_OPTIONS,
    )

    op.create_table(
        "signal_intake_results",
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_event_id", sa.String(64), nullable=False),
        sa.Column("command_fingerprint", sa.String(64), nullable=False),
        sa.Column("signal_event_id", sa.String(36), nullable=False),
        sa.Column("alert_id", sa.String(36), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "char_length(source_event_id) = 64",
            name=op.f("ck_signal_intake_results_intake_source_event_id"),
        ),
        sa.CheckConstraint(
            "char_length(command_fingerprint) = 64",
            name=op.f("ck_signal_intake_results_intake_command_fingerprint"),
        ),
        sa.CheckConstraint(
            f"outcome IN ({PROJECTION_OUTCOMES})",
            name=op.f("ck_signal_intake_results_intake_projection_outcome"),
        ),
        sa.ForeignKeyConstraint(
            ["signal_event_id"],
            ["signal_events.id"],
            name=op.f("fk_signal_intake_results_signal_event_id_signal_events"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
            name=op.f("fk_signal_intake_results_alert_id_alerts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("source", "source_event_id", name=op.f("pk_signal_intake_results")),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_signal_intake_results_signal_event_id",
        "signal_intake_results",
        ["signal_event_id"],
    )
    op.create_index("ix_signal_intake_results_alert_id", "signal_intake_results", ["alert_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.String(37), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("details", mysql.JSON(), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_audit_events_resource", "audit_events", ["resource_type", "resource_id"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("signal_intake_results")
    op.drop_table("ingestion_keys")
    op.drop_table("diagnosis_runs")
    op.drop_table("incidents")
    op.drop_table("alerts")
    op.drop_table("signal_events")
