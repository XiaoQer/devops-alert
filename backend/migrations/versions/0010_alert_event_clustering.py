"""建立告警事件聚类持久化模型。

Revision ID: 0010_alert_event_clustering
Revises: 0009_alert_source_environment
Create Date: 2026-08-27
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0010_alert_event_clustering"
down_revision: str | None = "0009_alert_source_environment"
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
        "alert_groups",
        sa.Column("profile_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column("alert_groups", sa.Column("forming_until", utc_datetime(), nullable=True))
    op.add_column("alert_groups", sa.Column("observing_until", utc_datetime(), nullable=True))
    op.add_column("alert_groups", sa.Column("closed_at", utc_datetime(), nullable=True))
    op.add_column(
        "alert_groups",
        sa.Column("member_limit", sa.Integer(), server_default="1000", nullable=False),
    )
    op.add_column("alert_groups", sa.Column("continuation_group_id", sa.String(36), nullable=True))
    op.add_column(
        "alert_groups",
        sa.Column("pending_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_foreign_key(
        op.f("fk_alert_groups_continuation_group_id_alert_groups"),
        "alert_groups",
        "alert_groups",
        ["continuation_group_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_alert_groups_alert_group_event_bounds"),
        "alert_groups",
        "profile_version >= 1 AND member_limit BETWEEN 1 AND 1000 "
        "AND pending_count BETWEEN 0 AND member_limit",
    )
    op.drop_constraint(op.f("ck_alert_groups_alert_group_state"), "alert_groups", type_="check")
    op.execute(
        sa.text(
            "UPDATE alert_groups SET state='CLOSED', closed_at=state_changed_at "
            "WHERE state='RESOLVED'"
        )
    )
    op.create_check_constraint(
        op.f("ck_alert_groups_alert_group_state"),
        "alert_groups",
        "state IN ('FORMING', 'ACTIVE', 'OBSERVING', 'CLOSED')",
    )

    op.create_table(
        "alert_event_profiles",
        sa.Column("alert_group_id", sa.String(36), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("services", sa.JSON(), nullable=False),
        sa.Column("entity_keys", sa.JSON(), nullable=False),
        sa.Column("scope_types", sa.JSON(), nullable=False),
        sa.Column("topology_edges", sa.JSON(), nullable=False),
        sa.Column("problem_types", sa.JSON(), nullable=False),
        sa.Column("symptoms", sa.JSON(), nullable=False),
        sa.Column("normalized_text", sa.String(2048), nullable=False),
        sa.Column("first_observed_at", utc_datetime(), nullable=False),
        sa.Column("last_observed_at", utc_datetime(), nullable=False),
        sa.Column("auto_confirmed_count", sa.Integer(), nullable=False),
        sa.Column("manual_confirmed_count", sa.Integer(), nullable=False),
        sa.Column("pending_count", sa.Integer(), nullable=False),
        sa.Column("rule_version", sa.String(32), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "profile_version >= 1", name=op.f("ck_alert_event_profiles_profile_version")
        ),
        sa.CheckConstraint(
            "JSON_TYPE(services) = 'ARRAY'", name=op.f("ck_alert_event_profiles_services_array")
        ),
        sa.CheckConstraint(
            "JSON_TYPE(entity_keys) = 'ARRAY'",
            name=op.f("ck_alert_event_profiles_entity_keys_array"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(scope_types) = 'ARRAY'",
            name=op.f("ck_alert_event_profiles_scope_types_array"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(topology_edges) = 'ARRAY'",
            name=op.f("ck_alert_event_profiles_topology_edges_array"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(problem_types) = 'ARRAY'",
            name=op.f("ck_alert_event_profiles_problem_types_array"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(symptoms) = 'ARRAY'", name=op.f("ck_alert_event_profiles_symptoms_array")
        ),
        sa.CheckConstraint(
            "auto_confirmed_count >= 0 AND manual_confirmed_count >= 0 AND pending_count >= 0",
            name=op.f("ck_alert_event_profiles_member_counts"),
        ),
        sa.ForeignKeyConstraint(
            ["alert_group_id"],
            ["alert_groups.id"],
            name=op.f("fk_alert_event_profiles_alert_group_id_alert_groups"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "alert_group_id", "profile_version", name=op.f("pk_alert_event_profiles")
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_alert_event_profiles_group_created",
        "alert_event_profiles",
        ["alert_group_id", "created_at"],
    )

    op.create_table(
        "alert_event_membership_decisions",
        sa.Column("id", sa.String(37), nullable=False),
        sa.Column("alert_id", sa.String(36), nullable=False),
        sa.Column("alert_cycle", sa.Integer(), nullable=False),
        sa.Column("alert_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("candidate_group_ids", sa.JSON(), nullable=False),
        sa.Column("selected_group_id", sa.String(36), nullable=True),
        sa.Column("selected_group_version", sa.Integer(), nullable=True),
        sa.Column("rule_version", sa.String(32), nullable=False),
        sa.Column("scores", sa.JSON(), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.String(500), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "alert_cycle >= 1 AND alert_version >= 1",
            name=op.f("ck_alert_event_membership_decisions_alert_version"),
        ),
        sa.CheckConstraint(
            "state IN ('AUTO_CONFIRMED', 'MANUAL_CONFIRMED', 'PENDING', 'REMOVED')",
            name=op.f("ck_alert_event_membership_decisions_state"),
        ),
        sa.CheckConstraint(
            "(state = 'PENDING' AND selected_group_id IS NULL "
            "AND selected_group_version IS NULL) OR "
            "(state IN ('AUTO_CONFIRMED', 'MANUAL_CONFIRMED', 'REMOVED') "
            "AND selected_group_id IS NOT NULL AND selected_group_version IS NOT NULL)",
            name=op.f("ck_alert_event_membership_decisions_selection_pair"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(candidate_group_ids) = 'ARRAY'",
            name=op.f("ck_alert_event_membership_decisions_candidates_array"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(scores) = 'OBJECT'",
            name=op.f("ck_alert_event_membership_decisions_scores_object"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(reason_codes) = 'ARRAY'",
            name=op.f("ck_alert_event_membership_decisions_reasons_array"),
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
            name=op.f("fk_alert_event_membership_decisions_alert_id_alerts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["selected_group_id"],
            ["alert_groups.id"],
            name=op.f("fk_alert_event_membership_decisions_selected_group_id_alert_groups"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_event_membership_decisions")),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_alert_event_membership_alert",
        "alert_event_membership_decisions",
        ["alert_id", "alert_cycle", "created_at"],
    )
    op.create_index(
        "ix_alert_event_membership_selected",
        "alert_event_membership_decisions",
        ["selected_group_id", "created_at"],
    )

    op.create_table(
        "alert_event_operations",
        sa.Column("id", sa.String(37), nullable=False),
        sa.Column("scope", sa.String(128), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("command_fingerprint", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("source_group_id", sa.String(36), nullable=False),
        sa.Column("target_group_id", sa.String(36), nullable=True),
        sa.Column("before_versions", sa.JSON(), nullable=False),
        sa.Column("after_versions", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('CONFIRM', 'SPLIT', 'MERGE')", name=op.f("ck_alert_event_operations_kind")
        ),
        sa.CheckConstraint(
            "char_length(idempotency_key_hash) = 64 AND char_length(command_fingerprint) = 64",
            name=op.f("ck_alert_event_operations_fingerprints"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(before_versions) = 'OBJECT'",
            name=op.f("ck_alert_event_operations_before_versions_object"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(after_versions) = 'OBJECT'",
            name=op.f("ck_alert_event_operations_after_versions_object"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(result) = 'OBJECT'", name=op.f("ck_alert_event_operations_result_object")
        ),
        sa.ForeignKeyConstraint(
            ["source_group_id"],
            ["alert_groups.id"],
            name=op.f("fk_alert_event_operations_source_group_id_alert_groups"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_group_id"],
            ["alert_groups.id"],
            name=op.f("fk_alert_event_operations_target_group_id_alert_groups"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_event_operations")),
        sa.UniqueConstraint("scope", "idempotency_key_hash", name="alert_event_operation_key"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_alert_event_operations_source",
        "alert_event_operations",
        ["source_group_id", "created_at"],
    )

    op.create_table(
        "alert_event_lifecycle_jobs",
        sa.Column("id", sa.String(37), nullable=False),
        sa.Column("alert_group_id", sa.String(36), nullable=False),
        sa.Column("target_group_version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
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
            "action IN ('FORMING_COMPLETE', 'OBSERVATION_COMPLETE')",
            name=op.f("ck_alert_event_lifecycle_jobs_action"),
        ),
        sa.CheckConstraint(
            "state IN ('PENDING', 'LEASED', 'SUCCEEDED', 'FAILED')",
            name=op.f("ck_alert_event_lifecycle_jobs_state"),
        ),
        sa.CheckConstraint(
            "target_group_version >= 1", name=op.f("ck_alert_event_lifecycle_jobs_target_version")
        ),
        sa.CheckConstraint(
            "attempts BETWEEN 0 AND 5", name=op.f("ck_alert_event_lifecycle_jobs_attempts")
        ),
        sa.CheckConstraint(
            "(state IN ('PENDING', 'LEASED') AND active_slot = 1) OR "
            "(state IN ('SUCCEEDED', 'FAILED') AND active_slot IS NULL)",
            name=op.f("ck_alert_event_lifecycle_jobs_active_slot"),
        ),
        sa.CheckConstraint(
            "(state = 'LEASED' AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(state <> 'LEASED' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name=op.f("ck_alert_event_lifecycle_jobs_lease"),
        ),
        sa.ForeignKeyConstraint(
            ["alert_group_id"],
            ["alert_groups.id"],
            name=op.f("fk_alert_event_lifecycle_jobs_alert_group_id_alert_groups"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_event_lifecycle_jobs")),
        sa.UniqueConstraint(
            "alert_group_id", "action", "active_slot", name="alert_event_lifecycle_active"
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_alert_event_lifecycle_claim",
        "alert_event_lifecycle_jobs",
        ["state", "available_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("alert_event_lifecycle_jobs")
    op.drop_table("alert_event_operations")
    op.drop_table("alert_event_membership_decisions")
    op.drop_table("alert_event_profiles")

    op.drop_constraint(op.f("ck_alert_groups_alert_group_state"), "alert_groups", type_="check")
    op.execute(sa.text("UPDATE alert_groups SET state='ACTIVE' WHERE state='FORMING'"))
    op.execute(
        sa.text("UPDATE alert_groups SET state='RESOLVED' WHERE state IN ('OBSERVING', 'CLOSED')")
    )
    op.create_check_constraint(
        op.f("ck_alert_groups_alert_group_state"),
        "alert_groups",
        "state IN ('ACTIVE', 'RESOLVED')",
    )
    op.drop_constraint(
        op.f("ck_alert_groups_alert_group_event_bounds"), "alert_groups", type_="check"
    )
    op.drop_constraint(
        op.f("fk_alert_groups_continuation_group_id_alert_groups"),
        "alert_groups",
        type_="foreignkey",
    )
    for column_name in (
        "pending_count",
        "continuation_group_id",
        "member_limit",
        "closed_at",
        "observing_until",
        "forming_until",
        "profile_version",
    ):
        op.drop_column("alert_groups", column_name)
