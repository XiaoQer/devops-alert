"""增加告警源身份、凭据、接收记录和管理操作。

Revision ID: 0005_alert_sources
Revises: 0004_incident_lifecycle
Create Date: 2026-08-26
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0005_alert_sources"
down_revision: str | None = "0004_incident_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MANUAL_SOURCE_ID = "src_00000000000000000000000000000001"
ALERTMANAGER_SOURCE_ID = "src_00000000000000000000000000000002"
CLOUDEVENTS_SOURCE_ID = "src_00000000000000000000000000000003"
TABLE_OPTIONS: dict[str, Any] = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_bin",
}


def utc_datetime() -> mysql.DATETIME:
    return mysql.DATETIME(fsp=6)


def _source_id_case(column_name: str = "source") -> str:
    return (
        f"CASE {column_name} "
        f"WHEN 'manual' THEN '{MANUAL_SOURCE_ID}' "
        f"WHEN 'alertmanager' THEN '{ALERTMANAGER_SOURCE_ID}' "
        f"WHEN 'cloudevents' THEN '{CLOUDEVENTS_SOURCE_ID}' END"
    )


def upgrade() -> None:
    op.create_table(
        "alert_sources",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("management_type", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("last_accepted_at", utc_datetime(), nullable=True),
        sa.Column("last_rejected_at", utc_datetime(), nullable=True),
        sa.Column("last_validated_at", utc_datetime(), nullable=True),
        sa.Column("accepted_requests", sa.BigInteger(), nullable=False),
        sa.Column("rejected_requests", sa.BigInteger(), nullable=False),
        sa.Column("opened_count", sa.BigInteger(), nullable=False),
        sa.Column("updated_count", sa.BigInteger(), nullable=False),
        sa.Column("resolved_count", sa.BigInteger(), nullable=False),
        sa.Column("replayed_count", sa.BigInteger(), nullable=False),
        sa.Column("ignored_count", sa.BigInteger(), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("updated_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('ALERTMANAGER', 'CLOUDEVENTS', 'MANUAL')",
            name=op.f("ck_alert_sources_source_type"),
        ),
        sa.CheckConstraint(
            "management_type IN ('USER_MANAGED', 'SYSTEM_MANAGED')",
            name=op.f("ck_alert_sources_management_type"),
        ),
        sa.CheckConstraint(
            "state IN ('ENABLED', 'DISABLED')",
            name=op.f("ck_alert_sources_state"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_alert_sources_version")),
        sa.CheckConstraint(
            "accepted_requests >= 0 AND rejected_requests >= 0 "
            "AND opened_count >= 0 AND updated_count >= 0 AND resolved_count >= 0 "
            "AND replayed_count >= 0 AND ignored_count >= 0",
            name=op.f("ck_alert_sources_non_negative_counts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_sources")),
        sa.UniqueConstraint("name", name="alert_source_name"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_alert_sources_state", "alert_sources", ["state"])
    op.create_index("ix_alert_sources_source_type", "alert_sources", ["source_type"])

    op.execute(
        sa.text(
            "INSERT INTO alert_sources "
            "(id, name, source_type, management_type, state, version, "
            "accepted_requests, rejected_requests, opened_count, updated_count, "
            "resolved_count, replayed_count, ignored_count, created_at, updated_at) VALUES "
            f"('{MANUAL_SOURCE_ID}', '人工报告', 'MANUAL', 'SYSTEM_MANAGED', 'ENABLED', 1, "
            "0, 0, 0, 0, 0, 0, 0, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)), "
            f"('{ALERTMANAGER_SOURCE_ID}', 'Alertmanager 兼容接入', 'ALERTMANAGER', "
            "'SYSTEM_MANAGED', 'ENABLED', 1, 0, 0, 0, 0, 0, 0, 0, "
            "UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)), "
            f"('{CLOUDEVENTS_SOURCE_ID}', 'CloudEvents 兼容接入', 'CLOUDEVENTS', "
            "'SYSTEM_MANAGED', 'ENABLED', 1, 0, 0, 0, 0, 0, 0, 0, "
            "UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))"
        )
    )

    op.create_table(
        "alert_source_credentials",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("alert_source_id", sa.String(36), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("last_used_at", utc_datetime(), nullable=True),
        sa.Column("revoked_at", utc_datetime(), nullable=True),
        sa.Column("revoked_by", sa.String(128), nullable=True),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('ACTIVE', 'REVOKED')",
            name=op.f("ck_alert_source_credentials_state"),
        ),
        sa.CheckConstraint(
            "(state = 'ACTIVE' AND revoked_at IS NULL AND revoked_by IS NULL) OR "
            "(state = 'REVOKED' AND revoked_at IS NOT NULL AND revoked_by IS NOT NULL)",
            name=op.f("ck_alert_source_credentials_revocation_pair"),
        ),
        sa.CheckConstraint(
            "char_length(token_digest) = 64",
            name=op.f("ck_alert_source_credentials_token_digest"),
        ),
        sa.ForeignKeyConstraint(
            ["alert_source_id"], ["alert_sources.id"],
            name=op.f("fk_alert_source_credentials_alert_source_id_alert_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_source_credentials")),
        sa.UniqueConstraint("token_digest", name="alert_source_credential_digest"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_alert_source_credentials_source_state",
        "alert_source_credentials",
        ["alert_source_id", "state"],
    )

    op.create_table(
        "alert_source_receipts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("alert_source_id", sa.String(36), nullable=False),
        sa.Column("adapter_type", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("input_count", sa.Integer(), nullable=False),
        sa.Column("opened_count", sa.Integer(), nullable=False),
        sa.Column("updated_count", sa.Integer(), nullable=False),
        sa.Column("resolved_count", sa.Integer(), nullable=False),
        sa.Column("replayed_count", sa.Integer(), nullable=False),
        sa.Column("ignored_count", sa.Integer(), nullable=False),
        sa.Column("received_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "adapter_type IN ('ALERTMANAGER', 'CLOUDEVENTS', 'MANUAL')",
            name=op.f("ck_alert_source_receipts_adapter_type"),
        ),
        sa.CheckConstraint(
            "outcome IN ('ACCEPTED', 'REPLAYED', 'VALIDATED', 'PAYLOAD_REJECTED', "
            "'SOURCE_DISABLED', 'PROCESSING_FAILED')",
            name=op.f("ck_alert_source_receipts_outcome"),
        ),
        sa.CheckConstraint(
            "input_count >= 0 AND opened_count >= 0 AND updated_count >= 0 "
            "AND resolved_count >= 0 AND replayed_count >= 0 AND ignored_count >= 0",
            name=op.f("ck_alert_source_receipts_non_negative_counts"),
        ),
        sa.ForeignKeyConstraint(
            ["alert_source_id"], ["alert_sources.id"],
            name=op.f("fk_alert_source_receipts_alert_source_id_alert_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_source_receipts")),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_alert_source_receipts_source_time",
        "alert_source_receipts",
        ["alert_source_id", "received_at", "id"],
    )

    op.create_table(
        "alert_source_operations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("command_fingerprint", sa.String(64), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("alert_source_id", sa.String(36), nullable=False),
        sa.Column("credential_id", sa.String(36), nullable=True),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("completed_at", utc_datetime(), nullable=False),
        sa.CheckConstraint(
            "action IN ('CREATE', 'UPDATE', 'ROTATE', 'REVOKE')",
            name=op.f("ck_alert_source_operations_action"),
        ),
        sa.CheckConstraint(
            "char_length(idempotency_key_hash) = 64 "
            "AND char_length(command_fingerprint) = 64",
            name=op.f("ck_alert_source_operations_hashes"),
        ),
        sa.CheckConstraint(
            "result_version >= 1",
            name=op.f("ck_alert_source_operations_result_version"),
        ),
        sa.ForeignKeyConstraint(
            ["alert_source_id"], ["alert_sources.id"],
            name=op.f("fk_alert_source_operations_alert_source_id_alert_sources"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["alert_source_credentials.id"],
            name=op.f("fk_alert_source_operations_credential_id_alert_source_credentials"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_source_operations")),
        sa.UniqueConstraint("scope", "idempotency_key_hash", name="alert_source_operation_key"),
        **TABLE_OPTIONS,
    )

    for table_name in ("signal_events", "alerts", "signal_intake_results", "correlation_jobs"):
        op.add_column(table_name, sa.Column("alert_source_id", sa.String(36), nullable=True))

    op.execute(sa.text(f"UPDATE signal_events SET alert_source_id = {_source_id_case()}"))
    op.execute(sa.text(f"UPDATE alerts SET alert_source_id = {_source_id_case()}"))
    op.execute(sa.text(f"UPDATE signal_intake_results SET alert_source_id = {_source_id_case()}"))
    op.execute(
        sa.text(
            "UPDATE correlation_jobs AS jobs JOIN alerts ON alerts.id = jobs.alert_id "
            "SET jobs.alert_source_id = alerts.alert_source_id"
        )
    )

    for table_name in ("signal_events", "alerts", "signal_intake_results", "correlation_jobs"):
        op.alter_column(
            table_name,
            "alert_source_id",
            existing_type=sa.String(36),
            nullable=False,
        )
        op.create_foreign_key(
            op.f(f"fk_{table_name}_alert_source_id_alert_sources"),
            table_name,
            "alert_sources",
            ["alert_source_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.drop_constraint("source_identity", "signal_events", type_="unique")
    op.create_unique_constraint(
        "signal_source_identity",
        "signal_events",
        ["alert_source_id", "source", "source_event_id"],
    )
    op.drop_constraint("alert_source_identity", "alerts", type_="unique")
    op.create_unique_constraint(
        "alert_source_identity",
        "alerts",
        ["alert_source_id", "source", "source_instance", "source_alert_key"],
    )
    op.drop_constraint("pk_signal_intake_results", "signal_intake_results", type_="primary")
    op.create_primary_key(
        "pk_signal_intake_results",
        "signal_intake_results",
        ["alert_source_id", "source", "source_event_id"],
    )
    op.create_index(
        "ix_correlation_jobs_alert_source_id",
        "correlation_jobs",
        ["alert_source_id"],
    )


def downgrade() -> None:
    for table_name in ("correlation_jobs", "signal_intake_results", "alerts", "signal_events"):
        op.drop_constraint(
            op.f(f"fk_{table_name}_alert_source_id_alert_sources"),
            table_name,
            type_="foreignkey",
        )

    op.drop_index("ix_correlation_jobs_alert_source_id", table_name="correlation_jobs")
    op.drop_constraint("pk_signal_intake_results", "signal_intake_results", type_="primary")
    op.create_primary_key(
        "pk_signal_intake_results", "signal_intake_results", ["source", "source_event_id"]
    )
    op.drop_constraint("alert_source_identity", "alerts", type_="unique")
    op.create_unique_constraint(
        "alert_source_identity", "alerts", ["source", "source_instance", "source_alert_key"]
    )
    op.drop_constraint("signal_source_identity", "signal_events", type_="unique")
    op.create_unique_constraint("source_identity", "signal_events", ["source", "source_event_id"])

    for table_name in ("correlation_jobs", "signal_intake_results", "alerts", "signal_events"):
        op.drop_column(table_name, "alert_source_id")

    op.drop_table("alert_source_operations")
    op.drop_table("alert_source_receipts")
    op.drop_table("alert_source_credentials")
    op.drop_table("alert_sources")
