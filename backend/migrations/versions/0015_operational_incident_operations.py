"""建立 Incident 人工操作幂等记录。

Revision ID: 0015_incident_ops
Revises: 0014_incident_reference_sequence
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0015_incident_ops"
down_revision: str | None = "0014_incident_reference_sequence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operational_incident_operations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("scope", sa.String(96), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("command_fingerprint", sa.String(64), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("incident_id", sa.String(36), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("completed_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "action IN ('ACKNOWLEDGE', 'RESOLVE')",
            name=op.f("ck_operational_incident_operations_action"),
        ),
        sa.CheckConstraint(
            "char_length(idempotency_key_hash) = 64 AND char_length(command_fingerprint) = 64",
            name=op.f("ck_operational_incident_operations_hashes"),
        ),
        sa.CheckConstraint(
            "result_version >= 1",
            name=op.f("ck_operational_incident_operations_result_version"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["operational_incidents.id"],
            name="fk_operational_incident_operations_incident",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operational_incident_operations")),
        sa.UniqueConstraint(
            "scope",
            "idempotency_key_hash",
            name="operational_incident_operation_key",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_bin",
    )


def downgrade() -> None:
    op.drop_table("operational_incident_operations")
