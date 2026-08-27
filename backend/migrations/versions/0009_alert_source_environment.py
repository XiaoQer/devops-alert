"""为告警源增加可信环境并允许稳定自定义环境代码。

Revision ID: 0009_alert_source_environment
Revises: 0008_problem_signature_grouping
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_alert_source_environment"
down_revision: str | None = "0008_problem_signature_grouping"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENVIRONMENT_TABLES: tuple[tuple[str, str], ...] = (
    ("signal_events", "signal_environment"),
    ("alerts", "alert_environment"),
    ("incidents", "incident_environment"),
    ("alert_groups", "alert_group_environment"),
    ("service_catalog_entries", "catalog_environment"),
)
ENVIRONMENT_PATTERN = "environment REGEXP '^[a-z][a-z0-9-]{0,31}$'"
LEGACY_ENVIRONMENTS = "environment IN ('production', 'staging', 'development', 'unknown')"


def upgrade() -> None:
    op.add_column("alert_sources", sa.Column("environment", sa.String(32), nullable=True))
    op.add_column("alert_sources", sa.Column("environment_name", sa.String(64), nullable=True))
    op.add_column(
        "alert_sources",
        sa.Column("environment_configured", sa.Boolean(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE alert_sources SET environment='unknown', "
            "environment_name='环境待配置', environment_configured=0"
        )
    )
    op.alter_column("alert_sources", "environment", existing_type=sa.String(32), nullable=False)
    op.alter_column(
        "alert_sources", "environment_name", existing_type=sa.String(64), nullable=False
    )
    op.alter_column(
        "alert_sources", "environment_configured", existing_type=sa.Boolean(), nullable=False
    )
    op.create_check_constraint(
        op.f("ck_alert_sources_environment"), "alert_sources", ENVIRONMENT_PATTERN
    )
    for table_name, constraint_name in ENVIRONMENT_TABLES:
        op.drop_constraint(op.f(f"ck_{table_name}_{constraint_name}"), table_name, type_="check")
        op.create_check_constraint(
            op.f(f"ck_{table_name}_{constraint_name}"), table_name, ENVIRONMENT_PATTERN
        )


def downgrade() -> None:
    connection = op.get_bind()
    incompatible_tables = [
        table_name
        for table_name, _ in ENVIRONMENT_TABLES
        if connection.scalar(
            sa.text(f"SELECT COUNT(*) FROM {table_name} WHERE NOT ({LEGACY_ENVIRONMENTS})")
        )
    ]
    if incompatible_tables:
        table_list = ", ".join(incompatible_tables)
        raise RuntimeError(
            f"无法降级: 旧版本不支持自定义环境; 请先处理以下表中的环境数据: {table_list}"
        )

    for table_name, constraint_name in ENVIRONMENT_TABLES:
        op.drop_constraint(op.f(f"ck_{table_name}_{constraint_name}"), table_name, type_="check")
        op.create_check_constraint(
            op.f(f"ck_{table_name}_{constraint_name}"), table_name, LEGACY_ENVIRONMENTS
        )
    op.drop_constraint(op.f("ck_alert_sources_environment"), "alert_sources", type_="check")
    op.drop_column("alert_sources", "environment_configured")
    op.drop_column("alert_sources", "environment_name")
    op.drop_column("alert_sources", "environment")
