"""建立独立 Incident 运营、异步任务与飞书协同表。

Revision ID: 0013_incident_operations_feishu
Revises: 0012_incident_rule_authoring
Create Date: 2026-09-01
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0013_incident_operations_feishu"
down_revision: str | None = "0012_incident_rule_authoring"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_OPTIONS: dict[str, Any] = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_bin",
}


def utc_datetime() -> mysql.DATETIME:
    return mysql.DATETIME(fsp=6)


def upgrade() -> None:
    op.create_table(
        "operational_incidents",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("reference", sa.String(22), nullable=False),
        sa.Column("title", sa.String(320), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("group_by", sa.String(16), nullable=False),
        sa.Column("group_key", sa.String(257), nullable=False),
        sa.Column("group_display_name", sa.String(257), nullable=False),
        sa.Column("incident_rule_id", sa.String(36), nullable=False),
        sa.Column("incident_rule_version", sa.Integer(), nullable=False),
        sa.Column("open_boundary_key", sa.String(64), nullable=True),
        sa.Column("alert_count", sa.Integer(), nullable=False),
        sa.Column("active_alert_count", sa.Integer(), nullable=False),
        sa.Column("distinct_alert_name_count", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("opened_at", utc_datetime(), nullable=False),
        sa.Column("acknowledged_at", utc_datetime(), nullable=True),
        sa.Column("resolved_at", utc_datetime(), nullable=True),
        sa.Column("resolution_summary", sa.String(2_000), nullable=True),
        sa.Column("last_alert_at", utc_datetime(), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("updated_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')",
            name=op.f("ck_operational_incidents_state"),
        ),
        sa.CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low')",
            name=op.f("ck_operational_incidents_severity"),
        ),
        sa.CheckConstraint(
            "environment REGEXP '^[a-z][a-z0-9-]{0,31}$'",
            name=op.f("ck_operational_incidents_environment"),
        ),
        sa.CheckConstraint(
            "group_by IN ('SERVICE', 'ENTITY')",
            name=op.f("ck_operational_incidents_group_by"),
        ),
        sa.CheckConstraint(
            "char_length(open_boundary_key) = 64 OR open_boundary_key IS NULL",
            name=op.f("ck_operational_incidents_open_boundary"),
        ),
        sa.CheckConstraint(
            "incident_rule_version >= 1 AND version >= 1",
            name=op.f("ck_operational_incidents_versions"),
        ),
        sa.CheckConstraint(
            "alert_count >= 1 AND active_alert_count >= 0 "
            "AND active_alert_count <= alert_count AND distinct_alert_name_count >= 1",
            name=op.f("ck_operational_incidents_counts"),
        ),
        sa.CheckConstraint(
            "(state = 'OPEN' AND open_boundary_key IS NOT NULL "
            "AND acknowledged_at IS NULL AND resolved_at IS NULL "
            "AND resolution_summary IS NULL) OR "
            "(state = 'ACKNOWLEDGED' AND open_boundary_key IS NOT NULL "
            "AND acknowledged_at IS NOT NULL AND resolved_at IS NULL "
            "AND resolution_summary IS NULL) OR "
            "(state = 'RESOLVED' AND open_boundary_key IS NULL "
            "AND resolved_at IS NOT NULL AND resolution_summary IS NOT NULL)",
            name=op.f("ck_operational_incidents_state_facts"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_rule_id"],
            ["incident_rules.id"],
            name="fk_operational_incidents_rule",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operational_incidents")),
        sa.UniqueConstraint("reference", name="operational_incident_reference"),
        sa.UniqueConstraint("open_boundary_key", name="operational_incident_open_boundary"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_operational_incidents_list",
        "operational_incidents",
        ["state", "updated_at", "id"],
    )
    op.create_index(
        "ix_operational_incidents_rule_group",
        "operational_incidents",
        ["incident_rule_id", "environment", "group_key"],
    )

    op.create_table(
        "operational_incident_alerts",
        sa.Column("incident_id", sa.String(36), nullable=False),
        sa.Column("alert_id", sa.String(36), nullable=False),
        sa.Column("incident_rule_version", sa.Integer(), nullable=False),
        sa.Column("first_trigger_window", sa.Boolean(), nullable=False),
        sa.Column("linked_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "incident_rule_version >= 1",
            name=op.f("ck_operational_incident_alerts_rule_version"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["operational_incidents.id"],
            name="fk_operational_incident_alerts_incident",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alert_lifecycles.id"],
            name="fk_operational_incident_alerts_alert",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "incident_id",
            "alert_id",
            name="operational_incident_alert_identity",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_operational_incident_alerts_alert",
        "operational_incident_alerts",
        ["alert_id", "incident_id"],
    )

    op.create_table(
        "operational_incident_activities",
        sa.Column("id", sa.String(37), nullable=False),
        sa.Column("incident_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("occurred_at", utc_datetime(), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("metadata", mysql.JSON(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('INCIDENT_CREATED', 'ALERTS_LINKED', 'SEVERITY_ESCALATED', "
            "'ALL_ALERTS_RECOVERED', 'ACKNOWLEDGED', 'RESOLVED', "
            "'FEISHU_MESSAGE_RECORDED', 'NOTIFICATION_FAILED')",
            name=op.f("ck_operational_incident_activities_kind"),
        ),
        sa.CheckConstraint(
            "actor_type IN ('SYSTEM', 'USER', 'FEISHU')",
            name=op.f("ck_operational_incident_activities_actor_type"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(metadata) = 'OBJECT' AND JSON_LENGTH(metadata) <= 20",
            name=op.f("ck_operational_incident_activities_metadata"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["operational_incidents.id"],
            name="fk_operational_incident_activities_incident",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operational_incident_activities")),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_operational_incident_activities_timeline",
        "operational_incident_activities",
        ["incident_id", "occurred_at", "id"],
    )

    _create_job_tables()
    _create_notification_tables()


def _create_job_tables() -> None:
    op.create_table(
        "incident_evaluation_jobs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("alert_id", sa.String(36), nullable=False),
        sa.Column("alert_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", utc_datetime(), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", utc_datetime(), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=True),
        sa.Column("reason_codes", mysql.JSON(), nullable=False),
        sa.Column("incident_ids", mysql.JSON(), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("updated_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('PENDING', 'LEASED', 'SUCCEEDED', 'FAILED')",
            name=op.f("ck_incident_evaluation_jobs_state"),
        ),
        sa.CheckConstraint(
            "alert_version >= 1 AND attempt_count BETWEEN 0 AND 10",
            name=op.f("ck_incident_evaluation_jobs_bounds"),
        ),
        sa.CheckConstraint(
            "(state = 'LEASED' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (state <> 'LEASED' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name=op.f("ck_incident_evaluation_jobs_lease"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(reason_codes) = 'ARRAY' AND JSON_LENGTH(reason_codes) <= 20 "
            "AND JSON_TYPE(incident_ids) = 'ARRAY' AND JSON_LENGTH(incident_ids) <= 100",
            name=op.f("ck_incident_evaluation_jobs_results"),
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alert_lifecycles.id"],
            name="fk_incident_evaluation_jobs_alert",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_evaluation_jobs")),
        sa.UniqueConstraint(
            "alert_id",
            "alert_version",
            name="incident_evaluation_alert_version",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_incident_evaluation_jobs_claim",
        "incident_evaluation_jobs",
        ["state", "available_at", "created_at"],
    )


def _create_notification_tables() -> None:
    op.create_table(
        "incident_notification_routes",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("chat_id", sa.String(128), nullable=False),
        sa.Column("chat_name", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("enabled_environment_key", sa.String(32), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("updated_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "environment REGEXP '^[a-z][a-z0-9-]{0,31}$'",
            name=op.f("ck_incident_notification_routes_environment"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_incident_notification_routes_version"),
        ),
        sa.CheckConstraint(
            "(enabled = 1 AND enabled_environment_key = environment) "
            "OR (enabled = 0 AND enabled_environment_key IS NULL)",
            name=op.f("ck_incident_notification_routes_enabled_key"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_notification_routes")),
        sa.UniqueConstraint(
            "enabled_environment_key",
            name="incident_route_enabled_environment",
        ),
        **TABLE_OPTIONS,
    )

    op.create_table(
        "incident_notification_outbox",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("incident_id", sa.String(36), nullable=False),
        sa.Column("activity_id", sa.String(37), nullable=False),
        sa.Column("notification_key", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("payload", mysql.JSON(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", utc_datetime(), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", utc_datetime(), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("feishu_message_id", sa.String(128), nullable=True),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("updated_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('CREATE_CARD', 'UPDATE_CARD', 'THREAD_REPLY')",
            name=op.f("ck_incident_notification_outbox_kind"),
        ),
        sa.CheckConstraint(
            "state IN ('PENDING', 'LEASED', 'SUCCEEDED', 'FAILED')",
            name=op.f("ck_incident_notification_outbox_state"),
        ),
        sa.CheckConstraint(
            "attempt_count BETWEEN 0 AND 10 AND char_length(notification_key) = 64",
            name=op.f("ck_incident_notification_outbox_bounds"),
        ),
        sa.CheckConstraint(
            "(state = 'LEASED' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (state <> 'LEASED' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name=op.f("ck_incident_notification_outbox_lease"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(payload) = 'OBJECT' AND JSON_LENGTH(payload) <= 30",
            name=op.f("ck_incident_notification_outbox_payload"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["operational_incidents.id"],
            name="fk_incident_notification_outbox_incident",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["activity_id"],
            ["operational_incident_activities.id"],
            name="fk_incident_notification_outbox_activity",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_notification_outbox")),
        sa.UniqueConstraint("notification_key", name="incident_notification_key"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_incident_notification_outbox_claim",
        "incident_notification_outbox",
        ["state", "available_at", "created_at"],
    )

    op.create_table(
        "incident_feishu_threads",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("incident_id", sa.String(36), nullable=False),
        sa.Column("route_id", sa.String(36), nullable=False),
        sa.Column("chat_id", sa.String(128), nullable=False),
        sa.Column("root_message_id", sa.String(128), nullable=False),
        sa.Column("last_synced_at", utc_datetime(), nullable=False),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("updated_at", utc_datetime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["operational_incidents.id"],
            name="fk_incident_feishu_threads_incident",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["route_id"],
            ["incident_notification_routes.id"],
            name="fk_incident_feishu_threads_route",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_feishu_threads")),
        sa.UniqueConstraint("incident_id", name="incident_feishu_thread_incident"),
        sa.UniqueConstraint(
            "chat_id",
            "root_message_id",
            name="incident_feishu_thread_message",
        ),
        **TABLE_OPTIONS,
    )

    op.create_table(
        "feishu_event_receipts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("received_at", utc_datetime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feishu_event_receipts")),
        sa.UniqueConstraint("event_id", name="feishu_event_identity"),
        **TABLE_OPTIONS,
    )


def downgrade() -> None:
    op.drop_table("feishu_event_receipts")
    op.drop_table("incident_feishu_threads")
    op.drop_table("incident_notification_outbox")
    op.drop_table("incident_notification_routes")
    op.drop_table("incident_evaluation_jobs")
    op.drop_table("operational_incident_activities")
    op.drop_table("operational_incident_alerts")
    op.drop_table("operational_incidents")
