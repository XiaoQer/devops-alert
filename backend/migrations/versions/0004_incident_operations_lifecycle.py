"""增加事故处置状态时间、业务活动和幂等操作记录。

Revision ID: 0004_incident_lifecycle
Revises: 0003_incident_assignment
Create Date: 2026-08-25
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0004_incident_lifecycle"
down_revision: str | None = "0003_incident_assignment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INCIDENT_STATES = (
    "'DETECTED', 'TRIAGING', 'INVESTIGATING', 'MITIGATING', "
    "'MONITORING_RECOVERY', 'RESOLVED', 'CLOSED'"
)
ACTIVITY_KINDS = (
    "'INCIDENT_CLAIMED', 'INCIDENT_RELEASED', 'STATE_TRANSITIONED', "
    "'NOTE_ADDED', 'INCIDENT_RESOLVED', 'INCIDENT_REOPENED', 'INCIDENT_CLOSED'"
)
OPERATION_KINDS = "'CLAIM', 'RELEASE', 'TRANSITION', 'ADD_NOTE', 'RESOLVE', 'REOPEN', 'CLOSE'"
NOTE_CATEGORIES = "'CURRENT_FINDING', 'ACTION_TAKEN', 'ACTION_RESULT', 'NEXT_STEP', 'GENERAL'"
RESOLUTION_CATEGORIES = "'RECOVERED', 'FALSE_POSITIVE', 'DUPLICATE', 'NO_ACTION', 'OTHER'"
TABLE_OPTIONS: dict[str, Any] = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_bin",
}


def utc_datetime() -> mysql.DATETIME:
    return mysql.DATETIME(fsp=6)


def upgrade() -> None:
    op.add_column("incidents", sa.Column("state_changed_at", utc_datetime(), nullable=True))
    op.add_column("incidents", sa.Column("resolved_at", utc_datetime(), nullable=True))
    op.add_column("incidents", sa.Column("closed_at", utc_datetime(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE incidents SET state_changed_at = created_at, "
            "resolved_at = CASE WHEN state IN ('RESOLVED', 'CLOSED') "
            "THEN created_at ELSE NULL END, "
            "closed_at = CASE WHEN state = 'CLOSED' THEN created_at ELSE NULL END"
        )
    )
    op.alter_column(
        "incidents",
        "state_changed_at",
        existing_type=utc_datetime(),
        nullable=False,
    )
    op.create_check_constraint(
        op.f("ck_incidents_incident_resolution_times"),
        "incidents",
        "(state NOT IN ('RESOLVED', 'CLOSED') "
        "AND resolved_at IS NULL AND closed_at IS NULL) OR "
        "(state = 'RESOLVED' AND resolved_at IS NOT NULL AND closed_at IS NULL) OR "
        "(state = 'CLOSED' AND resolved_at IS NOT NULL AND closed_at IS NOT NULL)",
    )

    op.create_table(
        "incident_activities",
        sa.Column("id", sa.String(37), nullable=False),
        sa.Column("incident_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("from_state", sa.String(32), nullable=True),
        sa.Column("to_state", sa.String(32), nullable=True),
        sa.Column("note_category", sa.String(32), nullable=True),
        sa.Column("message", sa.String(2_000), nullable=True),
        sa.Column("resolution_category", sa.String(32), nullable=True),
        sa.Column("resolution_actions", sa.String(4_000), nullable=True),
        sa.Column("root_cause", sa.String(4_000), nullable=True),
        sa.Column("incident_version", sa.Integer(), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            f"kind IN ({ACTIVITY_KINDS})",
            name=op.f("ck_incident_activities_incident_activity_kind"),
        ),
        sa.CheckConstraint(
            f"from_state IS NULL OR from_state IN ({INCIDENT_STATES})",
            name=op.f("ck_incident_activities_incident_activity_from_state"),
        ),
        sa.CheckConstraint(
            f"to_state IS NULL OR to_state IN ({INCIDENT_STATES})",
            name=op.f("ck_incident_activities_incident_activity_to_state"),
        ),
        sa.CheckConstraint(
            f"note_category IS NULL OR note_category IN ({NOTE_CATEGORIES})",
            name=op.f("ck_incident_activities_incident_activity_note_category"),
        ),
        sa.CheckConstraint(
            f"resolution_category IS NULL OR resolution_category IN ({RESOLUTION_CATEGORIES})",
            name=op.f("ck_incident_activities_incident_activity_resolution_category"),
        ),
        sa.CheckConstraint(
            "incident_version >= 1",
            name=op.f("ck_incident_activities_incident_activity_version"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_activities_incident_id_incidents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_activities")),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_incident_activities_incident_timeline",
        "incident_activities",
        ["incident_id", "created_at", "id"],
    )

    op.create_table(
        "incident_operations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("command_fingerprint", sa.String(64), nullable=False),
        sa.Column("incident_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("result_state", sa.String(32), nullable=False),
        sa.Column("result_assignee", sa.String(128), nullable=True),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("activity_id", sa.String(37), nullable=False),
        sa.Column("completed_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "char_length(idempotency_key_hash) = 64",
            name=op.f("ck_incident_operations_incident_operation_key_hash"),
        ),
        sa.CheckConstraint(
            "char_length(command_fingerprint) = 64",
            name=op.f("ck_incident_operations_incident_operation_fingerprint"),
        ),
        sa.CheckConstraint(
            f"action IN ({OPERATION_KINDS})",
            name=op.f("ck_incident_operations_incident_operation_action"),
        ),
        sa.CheckConstraint(
            f"result_state IN ({INCIDENT_STATES})",
            name=op.f("ck_incident_operations_incident_operation_result_state"),
        ),
        sa.CheckConstraint(
            "result_version >= 1",
            name=op.f("ck_incident_operations_incident_operation_result_version"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_operations_incident_id_incidents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["activity_id"],
            ["incident_activities.id"],
            name=op.f("fk_incident_operations_activity_id_incident_activities"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_operations")),
        sa.UniqueConstraint("scope", "idempotency_key_hash", name="incident_operation_scope_key"),
        sa.UniqueConstraint("activity_id", name="incident_operation_activity"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_incident_operations_incident_id",
        "incident_operations",
        ["incident_id"],
    )


def downgrade() -> None:
    op.drop_table("incident_operations")
    op.drop_table("incident_activities")
    op.drop_constraint(
        op.f("ck_incidents_incident_resolution_times"),
        "incidents",
        type_="check",
    )
    op.drop_column("incidents", "closed_at")
    op.drop_column("incidents", "resolved_at")
    op.drop_column("incidents", "state_changed_at")
