"""建立受控 Dify 诊断运行、快照、任务和审计表。

Revision ID: 0020_controlled_dify
Revises: 0019_monitoring_evidence
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0020_controlled_dify"
down_revision: str | None = "0019_monitoring_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"}


def _dt() -> mysql.DATETIME:
    return mysql.DATETIME(fsp=6)


def upgrade() -> None:
    op.create_table(
        "incident_diagnosis_runs",
        sa.Column("id", sa.String(37), primary_key=True),
        sa.Column(
            "incident_id",
            sa.String(36),
            sa.ForeignKey("operational_incidents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "evidence_run_id",
            sa.String(36),
            sa.ForeignKey("incident_evidence_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("active_slot", sa.Integer()),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("created_at", _dt(), nullable=False),
        sa.Column("started_at", _dt()),
        sa.Column("completed_at", _dt()),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "state IN ('QUEUED','RUNNING','REPORT_READY','REVIEW_REQUIRED','FAILED')",
            name="ck_incident_diagnosis_runs_state",
        ),
        sa.CheckConstraint(
            "(state IN ('QUEUED','RUNNING') AND active_slot <=> 1) OR "
            "(state NOT IN ('QUEUED','RUNNING') AND active_slot IS NULL)",
            name="ck_incident_diagnosis_runs_active_slot",
        ),
        sa.UniqueConstraint("incident_id", "active_slot", name="incident_active_diagnosis_run"),
        **OPTIONS,
    )
    op.create_index(
        "ix_incident_diagnosis_runs_incident",
        "incident_diagnosis_runs",
        ["incident_id", "created_at", "id"],
    )
    op.create_index(
        "ix_incident_diagnosis_runs_state", "incident_diagnosis_runs", ["state", "created_at"]
    )
    op.create_table(
        "incident_diagnosis_snapshots",
        sa.Column("id", sa.String(37), primary_key=True),
        sa.Column(
            "diagnosis_run_id",
            sa.String(37),
            sa.ForeignKey("incident_diagnosis_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("incident_snapshot", mysql.JSON(), nullable=False),
        sa.Column("evidence_snapshot", mysql.JSON(), nullable=False),
        sa.Column("scope_snapshot", mysql.JSON(), nullable=False),
        sa.Column("created_at", _dt(), nullable=False),
        sa.UniqueConstraint("diagnosis_run_id", name="diagnosis_snapshot_run"),
        **OPTIONS,
    )
    op.create_table(
        "incident_diagnosis_tasks",
        sa.Column("id", sa.String(38), primary_key=True),
        sa.Column(
            "diagnosis_run_id",
            sa.String(37),
            sa.ForeignKey("incident_diagnosis_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", _dt(), nullable=False),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_until", _dt()),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("created_at", _dt(), nullable=False),
        sa.Column("updated_at", _dt(), nullable=False),
        sa.UniqueConstraint("diagnosis_run_id", name="diagnosis_task_run"),
        **OPTIONS,
    )
    op.create_table(
        "incident_diagnosis_tool_receipts",
        sa.Column("id", sa.String(38), primary_key=True),
        sa.Column(
            "diagnosis_run_id",
            sa.String(37),
            sa.ForeignKey("incident_diagnosis_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_key", sa.String(128), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("created_at", _dt(), nullable=False),
        sa.UniqueConstraint("diagnosis_run_id", "tool_key", name="diagnosis_tool_receipt_key"),
        **OPTIONS,
    )
    op.create_table(
        "incident_diagnosis_reports",
        sa.Column(
            "diagnosis_run_id",
            sa.String(37),
            sa.ForeignKey("incident_diagnosis_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("report", mysql.JSON(), nullable=False),
        sa.Column("created_at", _dt(), nullable=False),
        **OPTIONS,
    )
    op.create_table(
        "incident_diagnosis_operations",
        sa.Column("id", sa.String(38), primary_key=True),
        sa.Column("scope", sa.String(96), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column(
            "diagnosis_run_id",
            sa.String(37),
            sa.ForeignKey("incident_diagnosis_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", _dt(), nullable=False),
        sa.UniqueConstraint("scope", "idempotency_key_hash", name="diagnosis_operation_key"),
        **OPTIONS,
    )


def downgrade() -> None:
    for table in (
        "incident_diagnosis_operations",
        "incident_diagnosis_reports",
        "incident_diagnosis_tool_receipts",
        "incident_diagnosis_tasks",
        "incident_diagnosis_snapshots",
    ):
        op.drop_table(table)
    op.drop_index("ix_incident_diagnosis_runs_state", table_name="incident_diagnosis_runs")
    op.drop_index("ix_incident_diagnosis_runs_incident", table_name="incident_diagnosis_runs")
    op.drop_table("incident_diagnosis_runs")
