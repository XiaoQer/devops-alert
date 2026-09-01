"""建立 Incident 日序列编号。

Revision ID: 0014_incident_reference_sequence
Revises: 0013_incident_operations_feishu
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0014_incident_reference_sequence"
down_revision: str | None = "0013_incident_operations_feishu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incident_reference_sequences",
        sa.Column("sequence_date", sa.Date(), nullable=False),
        sa.Column("last_value", sa.Integer(), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "`last_value` BETWEEN 1 AND 999999999",
            name=op.f("ck_incident_reference_sequences_value"),
        ),
        sa.PrimaryKeyConstraint(
            "sequence_date",
            name=op.f("pk_incident_reference_sequences"),
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_bin",
    )


def downgrade() -> None:
    op.drop_table("incident_reference_sequences")
