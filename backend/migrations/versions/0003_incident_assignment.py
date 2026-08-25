"""增加事故认领字段。

Revision ID: 0003_incident_assignment
Revises: 0002_service_catalog_correlation
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0003_incident_assignment"
down_revision: str | None = "0002_service_catalog_correlation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("assignee", sa.String(128), nullable=True))
    op.add_column(
        "incidents",
        sa.Column("claimed_at", mysql.DATETIME(fsp=6), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_incidents_incident_assignment_pair"),
        "incidents",
        "(assignee IS NULL AND claimed_at IS NULL) OR "
        "(assignee IS NOT NULL AND claimed_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_incidents_incident_assignment_pair"),
        "incidents",
        type_="check",
    )
    op.drop_column("incidents", "claimed_at")
    op.drop_column("incidents", "assignee")
