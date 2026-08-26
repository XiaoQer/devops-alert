"""增加告警组、风暴收敛任务和告警轮次。

Revision ID: 0006_alert_grouping_storm
Revises: 0005_alert_sources
Create Date: 2026-08-26
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0006_alert_grouping_storm"
down_revision: str | None = "0005_alert_sources"
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
    op.add_column(
        "alerts",
        sa.Column("cycle", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_check_constraint(op.f("ck_alerts_alert_cycle"), "alerts", "cycle >= 1")

    op.create_table(
        "alert_groups",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("storm_state", sa.String(16), nullable=False),
        sa.Column("rule_version", sa.String(32), nullable=False),
        sa.Column("service", sa.String(128), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("symptom", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("representative_alert_id", sa.String(36), nullable=False),
        sa.Column("incident_id", sa.String(36), nullable=True),
        sa.Column("first_observed_at", utc_datetime(), nullable=False),
        sa.Column("last_observed_at", utc_datetime(), nullable=False),
        sa.Column("state_changed_at", utc_datetime(), nullable=False),
        sa.Column("last_member_at", utc_datetime(), nullable=False),
        sa.Column("active_count", sa.Integer(), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("impacted_resource_count", sa.Integer(), nullable=False),
        sa.Column("desired_correlation_version", sa.Integer(), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.String(500), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("updated_at", utc_datetime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "state IN ('ACTIVE', 'RESOLVED')",
            name=op.f("ck_alert_groups_alert_group_state"),
        ),
        sa.CheckConstraint(
            "storm_state IN ('NORMAL', 'STORM')",
            name=op.f("ck_alert_groups_alert_group_storm_state"),
        ),
        sa.CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low')",
            name=op.f("ck_alert_groups_alert_group_severity"),
        ),
        sa.CheckConstraint(
            "environment IN ('production', 'staging', 'development', 'unknown')",
            name=op.f("ck_alert_groups_alert_group_environment"),
        ),
        sa.CheckConstraint(
            "active_count >= 0 AND total_count >= 1 "
            "AND active_count <= total_count AND impacted_resource_count >= 1",
            name=op.f("ck_alert_groups_alert_group_counts"),
        ),
        sa.CheckConstraint(
            "desired_correlation_version >= 0",
            name=op.f("ck_alert_groups_alert_group_target_version"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_alert_groups_alert_group_version")),
        sa.CheckConstraint(
            "JSON_TYPE(reason_codes) = 'ARRAY' AND JSON_LENGTH(reason_codes) BETWEEN 1 AND 10",
            name=op.f("ck_alert_groups_alert_group_reason_count"),
        ),
        sa.ForeignKeyConstraint(
            ["representative_alert_id"],
            ["alerts.id"],
            name=op.f("fk_alert_groups_representative_alert_id_alerts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_alert_groups_incident_id_incidents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_groups")),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_alert_groups_candidate",
        "alert_groups",
        ["state", "service", "environment", "symptom", "last_observed_at"],
    )
    op.create_index("ix_alert_groups_incident_id", "alert_groups", ["incident_id"])
    op.create_index("ix_alert_groups_storm_state", "alert_groups", ["storm_state"])

    op.create_table(
        "alert_group_members",
        sa.Column("alert_group_id", sa.String(36), nullable=False),
        sa.Column("alert_id", sa.String(36), nullable=False),
        sa.Column("alert_cycle", sa.Integer(), nullable=False),
        sa.Column("joined_alert_version", sa.Integer(), nullable=False),
        sa.Column("current_alert_version", sa.Integer(), nullable=False),
        sa.Column("current_state", sa.String(16), nullable=False),
        sa.Column("current_severity", sa.String(16), nullable=False),
        sa.Column("resource_type", sa.String(16), nullable=False),
        sa.Column("resource_name", sa.String(512), nullable=False),
        sa.Column("resource_key", sa.String(64), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("joined_at", utc_datetime(), nullable=False),
        sa.Column("updated_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "alert_cycle >= 1",
            name=op.f("ck_alert_group_members_alert_group_member_cycle_positive"),
        ),
        sa.CheckConstraint(
            "joined_alert_version >= 1 AND current_alert_version >= joined_alert_version",
            name=op.f("ck_alert_group_members_alert_group_member_versions"),
        ),
        sa.CheckConstraint(
            "current_state IN ('ACTIVE', 'RESOLVED', 'SUPPRESSED')",
            name=op.f("ck_alert_group_members_alert_group_member_state"),
        ),
        sa.CheckConstraint(
            "current_severity IN ('critical', 'high', 'medium', 'low')",
            name=op.f("ck_alert_group_members_alert_group_member_severity"),
        ),
        sa.CheckConstraint(
            "char_length(resource_key) = 64",
            name=op.f("ck_alert_group_members_alert_group_member_resource_key"),
        ),
        sa.ForeignKeyConstraint(
            ["alert_group_id"],
            ["alert_groups.id"],
            name=op.f("fk_alert_group_members_alert_group_id_alert_groups"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
            name=op.f("fk_alert_group_members_alert_id_alerts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "alert_group_id", "alert_id", "alert_cycle", name=op.f("pk_alert_group_members")
        ),
        sa.UniqueConstraint("alert_id", "alert_cycle", name="alert_group_member_cycle"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_alert_group_members_group_state",
        "alert_group_members",
        ["alert_group_id", "current_state", "current_severity"],
    )
    op.create_index(
        "ix_alert_group_members_resource",
        "alert_group_members",
        ["alert_group_id", "resource_key"],
    )

    op.create_table(
        "alert_grouping_jobs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("alert_id", sa.String(36), nullable=False),
        sa.Column("alert_cycle", sa.Integer(), nullable=False),
        sa.Column("alert_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", utc_datetime(), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", utc_datetime(), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("updated_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('PENDING', 'LEASED', 'SUCCEEDED', 'FAILED')",
            name=op.f("ck_alert_grouping_jobs_alert_grouping_job_state"),
        ),
        sa.CheckConstraint(
            "alert_cycle >= 1 AND alert_version >= 1",
            name=op.f("ck_alert_grouping_jobs_alert_grouping_job_alert_version_positive"),
        ),
        sa.CheckConstraint(
            "attempts BETWEEN 0 AND 5",
            name=op.f("ck_alert_grouping_jobs_alert_grouping_job_attempts"),
        ),
        sa.CheckConstraint(
            "(state = 'LEASED' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(state <> 'LEASED' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name=op.f("ck_alert_grouping_jobs_alert_grouping_job_lease"),
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
            name=op.f("fk_alert_grouping_jobs_alert_id_alerts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_grouping_jobs")),
        sa.UniqueConstraint("alert_id", "alert_version", name="alert_grouping_job_alert_version"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_alert_grouping_jobs_claim",
        "alert_grouping_jobs",
        ["state", "available_at", "created_at"],
    )
    op.create_index("ix_alert_grouping_jobs_alert_id", "alert_grouping_jobs", ["alert_id"])

    op.create_table(
        "alert_group_correlation_jobs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("alert_group_id", sa.String(36), nullable=False),
        sa.Column("target_group_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("active_slot", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", utc_datetime(), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", utc_datetime(), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("updated_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('PENDING', 'LEASED', 'SUCCEEDED', 'FAILED')",
            name=op.f("ck_alert_group_correlation_jobs_job_state"),
        ),
        sa.CheckConstraint(
            "target_group_version >= 1",
            name=op.f("ck_alert_group_correlation_jobs_target_version"),
        ),
        sa.CheckConstraint(
            "attempts BETWEEN 0 AND 5",
            name=op.f("ck_alert_group_correlation_jobs_attempts"),
        ),
        sa.CheckConstraint(
            "(state IN ('PENDING', 'LEASED') AND active_slot = 1) OR "
            "(state IN ('SUCCEEDED', 'FAILED') AND active_slot IS NULL)",
            name=op.f("ck_alert_group_correlation_jobs_active_slot"),
        ),
        sa.CheckConstraint(
            "(state = 'LEASED' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(state <> 'LEASED' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name=op.f("ck_alert_group_correlation_jobs_lease"),
        ),
        sa.ForeignKeyConstraint(
            ["alert_group_id"],
            ["alert_groups.id"],
            name=op.f("fk_alert_group_correlation_jobs_alert_group_id_alert_groups"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_group_correlation_jobs")),
        sa.UniqueConstraint(
            "alert_group_id", "active_slot", name="alert_group_correlation_job_active"
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_alert_group_correlation_jobs_claim",
        "alert_group_correlation_jobs",
        ["state", "available_at", "created_at"],
    )

    op.create_table(
        "alert_group_decisions",
        sa.Column("id", sa.String(37), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("alert_group_id", sa.String(36), nullable=False),
        sa.Column("group_version", sa.Integer(), nullable=False),
        sa.Column("incident_id", sa.String(36), nullable=True),
        sa.Column("outcome", sa.String(48), nullable=False),
        sa.Column("rule_version", sa.String(32), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("candidate_incident_ids", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.String(500), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "group_version >= 1",
            name=op.f("ck_alert_group_decisions_alert_group_decision_group_version"),
        ),
        sa.CheckConstraint(
            "outcome IN ('CREATED_NO_MATCH', 'LINKED_EXACT_SERVICE', 'LINKED_EXISTING', "
            "'CREATED_AMBIGUOUS', 'CREATED_DEPENDENCY_CANDIDATE', "
            "'REJECTED_INELIGIBLE', 'RECORDED_RESOLUTION', 'SUPERSEDED')",
            name=op.f("ck_alert_group_decisions_alert_group_decision_outcome"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(reason_codes) = 'ARRAY' AND JSON_LENGTH(reason_codes) BETWEEN 1 AND 10",
            name=op.f("ck_alert_group_decisions_alert_group_decision_reason_count"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(facts) = 'OBJECT'",
            name=op.f("ck_alert_group_decisions_alert_group_decision_facts_object"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(candidate_incident_ids) = 'ARRAY' "
            "AND JSON_LENGTH(candidate_incident_ids) <= 20",
            name=op.f("ck_alert_group_decisions_alert_group_decision_candidate_count"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["alert_group_correlation_jobs.id"],
            name=op.f("fk_alert_group_decisions_job_id_alert_group_correlation_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["alert_group_id"],
            ["alert_groups.id"],
            name=op.f("fk_alert_group_decisions_alert_group_id_alert_groups"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_alert_group_decisions_incident_id_incidents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_group_decisions")),
        sa.UniqueConstraint("job_id", name="alert_group_decision_job"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_alert_group_decisions_group_id", "alert_group_decisions", ["alert_group_id"]
    )
    op.create_index(
        "ix_alert_group_decisions_incident_id", "alert_group_decisions", ["incident_id"]
    )

    op.add_column(
        "incident_alert_links",
        sa.Column("group_decision_id", sa.String(37), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_incident_alert_links_group_decision_id_alert_group_decisions"),
        "incident_alert_links",
        "alert_group_decisions",
        ["group_decision_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_incident_alert_links_group_decision_id_alert_group_decisions"),
        "incident_alert_links",
        type_="foreignkey",
    )
    op.drop_column("incident_alert_links", "group_decision_id")

    op.drop_table("alert_group_decisions")
    op.drop_table("alert_group_correlation_jobs")
    op.drop_table("alert_grouping_jobs")
    op.drop_table("alert_group_members")
    op.drop_table("alert_groups")

    op.drop_constraint(op.f("ck_alerts_alert_cycle"), "alerts", type_="check")
    op.drop_column("alerts", "cycle")
