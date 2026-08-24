"""扩展多源信号和告警投影存储。

Revision ID: 0002_multi_source_signal_intake
Revises: 0001_initial_domain
Create Date: 2026-08-24
"""

from collections.abc import Sequence
from hashlib import sha256

import sqlalchemy as sa
from alembic import op

revision: str = "0002_multi_source_signal_intake"
down_revision: str | None = "0001_initial_domain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENT_TYPES = "'manual.reported', 'alert.firing', 'alert.resolved'"
PROJECTION_OUTCOMES = "'opened', 'updated', 'resolved', 'reopened', 'stale', 'orphan_resolved'"


def upgrade() -> None:
    op.add_column("signal_events", sa.Column("event_type", sa.String(32), nullable=True))
    op.add_column("alerts", sa.Column("source", sa.String(64), nullable=True))
    op.add_column("alerts", sa.Column("source_instance", sa.String(64), nullable=True))
    op.add_column("alerts", sa.Column("source_alert_key", sa.String(128), nullable=True))
    op.add_column(
        "alerts", sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=True)
    )

    connection = op.get_bind()
    signal_rows = connection.execute(
        sa.text("SELECT id, source, source_event_id FROM signal_events")
    ).mappings()
    signal_identity: dict[str, tuple[str, str]] = {}
    for row in signal_rows:
        source = str(row["source"])
        source_event_id = str(row["source_event_id"])
        signal_identity[str(row["id"])] = (source, source_event_id)
        connection.execute(
            sa.text(
                "UPDATE signal_events SET event_type = 'manual.reported' WHERE id = :signal_id"
            ),
            {"signal_id": row["id"]},
        )

    alert_rows = connection.execute(
        sa.text("SELECT id, signal_event_id, created_at FROM alerts")
    ).mappings()
    for row in alert_rows:
        source, source_event_id = signal_identity[str(row["signal_event_id"])]
        connection.execute(
            sa.text(
                "UPDATE alerts SET source = :source, source_instance = :source_instance, "
                "source_alert_key = :source_alert_key, state_changed_at = :state_changed_at "
                "WHERE id = :alert_id"
            ),
            {
                "source": source,
                "source_instance": sha256(source.encode("utf-8")).hexdigest(),
                "source_alert_key": sha256(source_event_id.encode("utf-8")).hexdigest(),
                "state_changed_at": row["created_at"],
                "alert_id": row["id"],
            },
        )

    op.alter_column("signal_events", "event_type", nullable=False)
    op.create_check_constraint(
        op.f("ck_signal_events_signal_event_type"),
        "signal_events",
        f"event_type IN ({EVENT_TYPES})",
    )
    for column in ("source", "source_instance", "source_alert_key", "state_changed_at"):
        op.alter_column("alerts", column, nullable=False)
    op.create_check_constraint(
        op.f("ck_alerts_alert_source_instance"),
        "alerts",
        "char_length(source_instance) = 64",
    )
    op.create_check_constraint(
        op.f("ck_alerts_alert_source_alert_key"),
        "alerts",
        "char_length(source_alert_key) >= 1",
    )
    op.create_unique_constraint(
        "alert_source_identity",
        "alerts",
        ["source", "source_instance", "source_alert_key"],
    )

    op.create_table(
        "signal_intake_results",
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_event_id", sa.String(64), nullable=False),
        sa.Column("command_fingerprint", sa.String(64), nullable=False),
        sa.Column("signal_event_id", sa.String(36), nullable=False),
        sa.Column("alert_id", sa.String(36), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "char_length(source_event_id) = 64",
            name=op.f("ck_signal_intake_results_intake_source_event_id"),
        ),
        sa.CheckConstraint(
            "char_length(command_fingerprint) = 64",
            name=op.f("ck_signal_intake_results_intake_command_fingerprint"),
        ),
        sa.CheckConstraint(
            f"outcome IN ({PROJECTION_OUTCOMES})",
            name=op.f("ck_signal_intake_results_intake_projection_outcome"),
        ),
        sa.ForeignKeyConstraint(
            ["signal_event_id"],
            ["signal_events.id"],
            name=op.f("fk_signal_intake_results_signal_event_id_signal_events"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
            name=op.f("fk_signal_intake_results_alert_id_alerts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("source", "source_event_id", name=op.f("pk_signal_intake_results")),
    )
    op.create_index(
        "ix_signal_intake_results_signal_event_id",
        "signal_intake_results",
        ["signal_event_id"],
    )
    op.create_index("ix_signal_intake_results_alert_id", "signal_intake_results", ["alert_id"])


def downgrade() -> None:
    op.drop_table("signal_intake_results")
    op.drop_constraint("alert_source_identity", "alerts", type_="unique")
    op.drop_constraint(op.f("ck_alerts_alert_source_alert_key"), "alerts", type_="check")
    op.drop_constraint(op.f("ck_alerts_alert_source_instance"), "alerts", type_="check")
    for column in ("state_changed_at", "source_alert_key", "source_instance", "source"):
        op.drop_column("alerts", column)
    op.drop_constraint(op.f("ck_signal_events_signal_event_type"), "signal_events", type_="check")
    op.drop_column("signal_events", "event_type")
