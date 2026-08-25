"""增加服务目录、持久关联任务和可解释关联记录。

Revision ID: 0002_service_catalog_correlation
Revises: 0001_mysql_initial
Create Date: 2026-08-25
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0002_service_catalog_correlation"
down_revision: str | None = "0001_mysql_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATALOG_STATES = "'ACTIVE', 'INACTIVE'"
ENVIRONMENTS = "'production', 'staging', 'development', 'unknown'"
JOB_STATES = "'PENDING', 'LEASED', 'SUCCEEDED', 'FAILED'"
CORRELATION_OUTCOMES = (
    "'CREATED_NO_MATCH', 'LINKED_EXACT_SERVICE', 'LINKED_EXISTING', "
    "'CREATED_AMBIGUOUS', 'CREATED_DEPENDENCY_CANDIDATE', "
    "'REJECTED_INELIGIBLE', 'RECORDED_RESOLUTION', 'SUPERSEDED'"
)
TABLE_OPTIONS: dict[str, Any] = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_bin",
}


def utc_datetime() -> mysql.DATETIME:
    return mysql.DATETIME(fsp=6)


def upgrade() -> None:
    op.create_table(
        "service_catalog_entries",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("service", sa.String(128), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("owner_team", sa.String(128), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("updated_at", utc_datetime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            f"state IN ({CATALOG_STATES})",
            name=op.f("ck_service_catalog_entries_catalog_state"),
        ),
        sa.CheckConstraint(
            f"environment IN ({ENVIRONMENTS})",
            name=op.f("ck_service_catalog_entries_catalog_environment"),
        ),
        sa.CheckConstraint(
            "char_length(service) >= 1",
            name=op.f("ck_service_catalog_entries_catalog_service_not_empty"),
        ),
        sa.CheckConstraint(
            "char_length(owner_team) >= 1",
            name=op.f("ck_service_catalog_entries_catalog_owner_not_empty"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_service_catalog_entries_catalog_version"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_catalog_entries")),
        sa.UniqueConstraint("service", "environment", name="service_catalog_identity"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_service_catalog_entries_state",
        "service_catalog_entries",
        ["state"],
    )
    op.create_index(
        "ix_service_catalog_entries_environment",
        "service_catalog_entries",
        ["environment"],
    )

    op.create_table(
        "service_catalog_state",
        sa.Column("id", sa.String(16), nullable=False),
        sa.Column("graph_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "id = 'global'",
            name=op.f("ck_service_catalog_state_catalog_state_singleton"),
        ),
        sa.CheckConstraint(
            "graph_version >= 1",
            name=op.f("ck_service_catalog_state_catalog_graph_version"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_catalog_state")),
        **TABLE_OPTIONS,
    )
    op.bulk_insert(
        sa.table(
            "service_catalog_state",
            sa.column("id", sa.String(16)),
            sa.column("graph_version", sa.Integer()),
            sa.column("updated_at", utc_datetime()),
        ),
        [{"id": "global", "graph_version": 1, "updated_at": datetime.now(UTC)}],
    )

    op.create_table(
        "service_dependencies",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("caller_service_id", sa.String(36), nullable=False),
        sa.Column("dependency_service_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("updated_at", utc_datetime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "caller_service_id <> dependency_service_id",
            name=op.f("ck_service_dependencies_dependency_not_self"),
        ),
        sa.CheckConstraint(
            f"state IN ({CATALOG_STATES})",
            name=op.f("ck_service_dependencies_dependency_state"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_service_dependencies_dependency_version"),
        ),
        sa.ForeignKeyConstraint(
            ["caller_service_id"],
            ["service_catalog_entries.id"],
            name=op.f("fk_service_dependencies_caller_service_id_service_catalog_entries"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dependency_service_id"],
            ["service_catalog_entries.id"],
            name=op.f("fk_service_dependencies_dependency_service_id_service_catalog_entries"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_dependencies")),
        sa.UniqueConstraint(
            "caller_service_id",
            "dependency_service_id",
            name="service_dependency_edge",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_service_dependencies_state", "service_dependencies", ["state"])
    op.create_index(
        "ix_service_dependencies_dependency",
        "service_dependencies",
        ["dependency_service_id"],
    )

    op.create_table(
        "correlation_jobs",
        sa.Column("id", sa.String(37), nullable=False),
        sa.Column("alert_id", sa.String(36), nullable=False),
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
            f"state IN ({JOB_STATES})",
            name=op.f("ck_correlation_jobs_correlation_job_state"),
        ),
        sa.CheckConstraint(
            "alert_version >= 1",
            name=op.f("ck_correlation_jobs_correlation_job_alert_version_positive"),
        ),
        sa.CheckConstraint(
            "attempts BETWEEN 0 AND 5",
            name=op.f("ck_correlation_jobs_correlation_job_attempts"),
        ),
        sa.CheckConstraint(
            "(state = 'LEASED' AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(state <> 'LEASED' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL)",
            name=op.f("ck_correlation_jobs_correlation_job_lease"),
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
            name=op.f("fk_correlation_jobs_alert_id_alerts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_correlation_jobs")),
        sa.UniqueConstraint(
            "alert_id",
            "alert_version",
            name="correlation_job_alert_version",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_correlation_jobs_claim",
        "correlation_jobs",
        ["state", "available_at", "created_at"],
    )
    op.create_index("ix_correlation_jobs_alert_id", "correlation_jobs", ["alert_id"])

    op.create_table(
        "correlation_decisions",
        sa.Column("id", sa.String(37), nullable=False),
        sa.Column("job_id", sa.String(37), nullable=False),
        sa.Column("alert_id", sa.String(36), nullable=False),
        sa.Column("alert_version", sa.Integer(), nullable=False),
        sa.Column("incident_id", sa.String(36), nullable=True),
        sa.Column("outcome", sa.String(48), nullable=False),
        sa.Column("rule_version", sa.String(32), nullable=False),
        sa.Column("reason_codes", mysql.JSON(), nullable=False),
        sa.Column("facts", mysql.JSON(), nullable=False),
        sa.Column("candidate_incident_ids", mysql.JSON(), nullable=False),
        sa.Column("explanation", sa.String(500), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "alert_version >= 1",
            name=op.f("ck_correlation_decisions_correlation_decision_alert_version"),
        ),
        sa.CheckConstraint(
            f"outcome IN ({CORRELATION_OUTCOMES})",
            name=op.f("ck_correlation_decisions_correlation_decision_outcome"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(reason_codes) = 'ARRAY' AND JSON_LENGTH(reason_codes) BETWEEN 1 AND 10",
            name=op.f("ck_correlation_decisions_correlation_decision_reason_count"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(facts) = 'OBJECT'",
            name=op.f("ck_correlation_decisions_correlation_decision_facts_object"),
        ),
        sa.CheckConstraint(
            "JSON_TYPE(candidate_incident_ids) = 'ARRAY' "
            "AND JSON_LENGTH(candidate_incident_ids) <= 20",
            name=op.f("ck_correlation_decisions_correlation_decision_candidate_count"),
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
            name=op.f("fk_correlation_decisions_alert_id_alerts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_correlation_decisions_incident_id_incidents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["correlation_jobs.id"],
            name=op.f("fk_correlation_decisions_job_id_correlation_jobs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_correlation_decisions")),
        sa.UniqueConstraint("job_id", name="correlation_decision_job"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_correlation_decisions_alert_id",
        "correlation_decisions",
        ["alert_id"],
    )
    op.create_index(
        "ix_correlation_decisions_incident_id",
        "correlation_decisions",
        ["incident_id"],
    )

    op.create_table(
        "incident_alert_links",
        sa.Column("incident_id", sa.String(36), nullable=False),
        sa.Column("alert_id", sa.String(36), nullable=False),
        sa.Column("relation", sa.String(16), nullable=False),
        sa.Column("decision_id", sa.String(37), nullable=True),
        sa.Column("linked_at", utc_datetime(), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "relation IN ('PRIMARY', 'RELATED')",
            name=op.f("ck_incident_alert_links_incident_alert_relation"),
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
            name=op.f("fk_incident_alert_links_alert_id_alerts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["correlation_decisions.id"],
            name=op.f("fk_incident_alert_links_decision_id_correlation_decisions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_alert_links_incident_id_incidents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "incident_id",
            "alert_id",
            name=op.f("pk_incident_alert_links"),
        ),
        sa.UniqueConstraint("alert_id", name="incident_alert_link_alert"),
        sa.UniqueConstraint("decision_id", name="incident_alert_link_decision"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_incident_alert_links_alert_id",
        "incident_alert_links",
        ["alert_id"],
    )
    op.execute(
        sa.text(
            "INSERT INTO incident_alert_links "
            "(incident_id, alert_id, relation, decision_id, linked_at, created_at) "
            "SELECT id, primary_alert_id, 'PRIMARY', NULL, created_at, created_at "
            "FROM incidents"
        )
    )


def downgrade() -> None:
    op.drop_table("incident_alert_links")
    op.drop_table("correlation_decisions")
    op.drop_table("correlation_jobs")
    op.drop_table("service_dependencies")
    op.drop_table("service_catalog_state")
    op.drop_table("service_catalog_entries")
