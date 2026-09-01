"""建立飞书通知路由操作幂等记录。

Revision ID: 0016_route_ops
Revises: 0015_incident_ops
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0016_route_ops"
down_revision: str | None = "0015_incident_ops"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incident_notification_route_operations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("scope", sa.String(96), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("command_fingerprint", sa.String(64), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("route_id", sa.String(36), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("completed_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "action IN ('CREATE', 'UPDATE')",
            name=op.f("ck_incident_notification_route_operations_action"),
        ),
        sa.CheckConstraint(
            "char_length(idempotency_key_hash) = 64 AND char_length(command_fingerprint) = 64",
            name=op.f("ck_incident_notification_route_operations_hashes"),
        ),
        sa.CheckConstraint(
            "result_version >= 1",
            name=op.f("ck_incident_notification_route_operations_result_version"),
        ),
        sa.ForeignKeyConstraint(
            ["route_id"],
            ["incident_notification_routes.id"],
            name="fk_incident_notification_route_operations_route",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_notification_route_operations")),
        sa.UniqueConstraint(
            "scope",
            "idempotency_key_hash",
            name="incident_notification_route_operation_key",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_bin",
    )


def downgrade() -> None:
    op.drop_table("incident_notification_route_operations")
