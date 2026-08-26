"""允许告警缺少服务并保存真实实体摘要。

Revision ID: 0007_entity_aware_alerts
Revises: 0006_alert_grouping_storm
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_entity_aware_alerts"
down_revision: str | None = "0006_alert_grouping_storm"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("signal_events", "alerts", "alert_groups")
CONSTRAINT_NAMES = {
    "signal_events": ("ck_signal_events_signal_entity_type", "ck_signal_events_signal_entity_key"),
    "alerts": ("ck_alerts_alert_entity_type", "ck_alerts_alert_entity_key"),
    "alert_groups": (
        "ck_alert_groups_alert_group_entity_type",
        "ck_alert_groups_alert_group_entity_key",
    ),
}


def upgrade() -> None:
    for table_name in TABLES:
        op.add_column(table_name, sa.Column("entity_type", sa.String(16), nullable=True))
        op.add_column(table_name, sa.Column("entity_key", sa.String(64), nullable=True))
        op.add_column(table_name, sa.Column("entity_display_name", sa.String(257), nullable=True))
        op.execute(
            sa.text(
                f"UPDATE {table_name} SET entity_type = 'SERVICE', "
                "entity_key = SHA2(CONCAT('entity.v1', CHAR(0), 'SERVICE', "
                "CHAR(0), service), 256), "
                "entity_display_name = service"
            )
        )
        op.alter_column(table_name, "entity_type", existing_type=sa.String(16), nullable=False)
        op.alter_column(table_name, "entity_key", existing_type=sa.String(64), nullable=False)
        op.alter_column(
            table_name, "entity_display_name", existing_type=sa.String(257), nullable=False
        )
        op.alter_column(table_name, "service", existing_type=sa.String(128), nullable=True)
        type_constraint, key_constraint = CONSTRAINT_NAMES[table_name]
        op.create_check_constraint(
            op.f(type_constraint),
            table_name,
            "entity_type IN ('SERVICE','WORKLOAD','POD','NODE','JOB',"
            "'INSTANCE','CLUSTER','UNKNOWN')",
        )
        op.create_check_constraint(op.f(key_constraint), table_name, "char_length(entity_key) = 64")

    op.drop_index("ix_alert_groups_candidate", table_name="alert_groups")
    op.create_index(
        "ix_alert_groups_candidate",
        "alert_groups",
        ["state", "entity_key", "environment", "symptom", "last_observed_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    for table_name in TABLES:
        missing = connection.scalar(
            sa.text(f"SELECT COUNT(*) FROM {table_name} WHERE service IS NULL")
        )
        if missing:
            raise RuntimeError("entity_aware_downgrade_requires_services")

    op.drop_index("ix_alert_groups_candidate", table_name="alert_groups")
    op.create_index(
        "ix_alert_groups_candidate",
        "alert_groups",
        ["state", "service", "environment", "symptom", "last_observed_at"],
    )
    for table_name in reversed(TABLES):
        type_constraint, key_constraint = CONSTRAINT_NAMES[table_name]
        op.drop_constraint(op.f(key_constraint), table_name, type_="check")
        op.drop_constraint(op.f(type_constraint), table_name, type_="check")
        op.alter_column(table_name, "service", existing_type=sa.String(128), nullable=False)
        op.drop_column(table_name, "entity_display_name")
        op.drop_column(table_name, "entity_key")
        op.drop_column(table_name, "entity_type")
