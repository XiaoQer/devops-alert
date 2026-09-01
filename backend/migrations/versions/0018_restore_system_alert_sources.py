"""恢复可能被历史数据清理误删的系统接入源。

Revision ID: 0018_restore_system_sources
Revises: 0017_feishu_activity_text
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import insert

revision: str = "0018_restore_system_sources"
down_revision: str | None = "0017_feishu_activity_text"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SYSTEM_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("src_00000000000000000000000000000001", "人工报告", "MANUAL"),
    (
        "src_00000000000000000000000000000002",
        "Alertmanager 兼容接入",
        "ALERTMANAGER",
    ),
    (
        "src_00000000000000000000000000000003",
        "CloudEvents 兼容接入",
        "CLOUDEVENTS",
    ),
)


def upgrade() -> None:
    alert_sources = sa.table(
        "alert_sources",
        sa.column("id"),
        sa.column("name"),
        sa.column("source_type"),
        sa.column("management_type"),
        sa.column("state"),
        sa.column("environment"),
        sa.column("environment_name"),
        sa.column("environment_configured"),
        sa.column("version"),
        sa.column("accepted_requests"),
        sa.column("rejected_requests"),
        sa.column("opened_count"),
        sa.column("updated_count"),
        sa.column("resolved_count"),
        sa.column("replayed_count"),
        sa.column("ignored_count"),
        sa.column("created_at"),
        sa.column("updated_at"),
    )
    connection = op.get_bind()
    existing_ids = set(connection.scalars(sa.select(alert_sources.c.id)))
    existing_names = set(connection.scalars(sa.select(alert_sources.c.name)))
    now = sa.func.utc_timestamp(6)
    rows: list[dict[str, object]] = []
    for source_id, name, source_type in SYSTEM_SOURCES:
        if source_id in existing_ids:
            continue
        restored_name = name
        suffix = 1
        while restored_name in existing_names:
            restored_name = f"{name} [system-{source_id[-4:]}-{suffix}]"
            suffix += 1
        rows.append(
            {
                "id": source_id,
                "name": restored_name,
                "source_type": source_type,
                "management_type": "SYSTEM_MANAGED",
                "state": "ENABLED",
                "environment": "unknown",
                "environment_name": "环境待配置",
                "environment_configured": False,
                "version": 1,
                "accepted_requests": 0,
                "rejected_requests": 0,
                "opened_count": 0,
                "updated_count": 0,
                "resolved_count": 0,
                "replayed_count": 0,
                "ignored_count": 0,
                "created_at": now,
                "updated_at": now,
            }
        )
        existing_names.add(restored_name)
    if rows:
        connection.execute(insert(alert_sources).values(rows).prefix_with("IGNORE"))


def downgrade() -> None:
    # 这是运行数据修复, 降级时保留已恢复且可能已被业务数据引用的系统来源.
    pass
