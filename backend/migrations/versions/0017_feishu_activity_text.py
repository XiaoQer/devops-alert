"""扩展飞书协同活动文本上限。

Revision ID: 0017_feishu_activity_text
Revises: 0016_route_ops
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_feishu_activity_text"
down_revision: str | None = "0016_route_ops"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "operational_incident_activities",
        "summary",
        existing_type=sa.String(length=500),
        type_=sa.String(length=4_000),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "operational_incident_activities",
        "summary",
        existing_type=sa.String(length=4_000),
        type_=sa.String(length=500),
        existing_nullable=False,
    )
