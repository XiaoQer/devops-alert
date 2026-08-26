from __future__ import annotations

from datetime import UTC, datetime

from alembic import command
from alembic.config import Config
from sqlalchemy import column, insert, inspect, select, table
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from incident_intelligence.domain.alert_sources import (
    ALERTMANAGER_COMPAT_SOURCE_ID,
    CLOUDEVENTS_COMPAT_SOURCE_ID,
    MANUAL_SYSTEM_SOURCE_ID,
)
from incident_intelligence.ids import new_id

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)
SOURCE_TABLES = {
    "alert_sources",
    "alert_source_credentials",
    "alert_source_receipts",
    "alert_source_operations",
}


def _seed_historical_manual_signal_and_alert(engine: Engine) -> tuple[str, str]:
    signal_id = new_id("sig")
    alert_id = new_id("alt")
    signal_events = table(
        "signal_events",
        *[
            column(name)
            for name in (
                "id",
                "source",
                "source_event_id",
                "event_type",
                "title",
                "summary",
                "severity",
                "service",
                "environment",
                "observed_at",
                "received_at",
                "facts",
                "payload_fingerprint",
                "created_at",
                "version",
            )
        ],
    )
    alerts = table(
        "alerts",
        *[
            column(name)
            for name in (
                "id",
                "signal_event_id",
                "source",
                "source_instance",
                "source_alert_key",
                "state",
                "title",
                "severity",
                "service",
                "environment",
                "first_observed_at",
                "last_observed_at",
                "state_changed_at",
                "created_at",
                "version",
            )
        ],
    )
    with Session(engine) as session:
        session.execute(
            insert(signal_events).values(
                id=signal_id,
                source="manual",
                source_event_id="historical-manual-source",
                event_type="manual.reported",
                title="人工报告",
                summary="人工报告摘要",
                severity="high",
                service="payment-api",
                environment="production",
                observed_at=NOW,
                received_at=NOW,
                facts="{}",
                payload_fingerprint="a" * 64,
                created_at=NOW,
                version=1,
            )
        )
        session.execute(
            insert(alerts).values(
                id=alert_id,
                signal_event_id=signal_id,
                source="manual",
                source_instance="b" * 64,
                source_alert_key="historical-manual-source",
                state="ACTIVE",
                title="人工报告",
                severity="high",
                service="payment-api",
                environment="production",
                first_observed_at=NOW,
                last_observed_at=NOW,
                state_changed_at=NOW,
                created_at=NOW,
                version=1,
            )
        )
        session.commit()
    return signal_id, alert_id


def test_upgrade_creates_source_tables_and_backfills_manual_identity(
    alembic_config: Config,
    mysql_engine: Engine,
) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "0004_incident_lifecycle")
    signal_id, alert_id = _seed_historical_manual_signal_and_alert(mysql_engine)
    try:
        command.upgrade(alembic_config, "head")

        inspector = inspect(mysql_engine)
        assert set(inspector.get_table_names()) >= SOURCE_TABLES
        source_columns = {item["name"] for item in inspector.get_columns("alert_sources")}
        assert {
            "id",
            "name",
            "source_type",
            "management_type",
            "state",
            "version",
            "last_accepted_at",
            "last_rejected_at",
            "last_validated_at",
            "accepted_requests",
            "rejected_requests",
            "opened_count",
            "updated_count",
            "resolved_count",
            "replayed_count",
            "ignored_count",
            "created_at",
            "updated_at",
        } == source_columns
        assert "alert_source_id" in {
            item["name"] for item in inspector.get_columns("signal_events")
        }
        assert "alert_source_id" in {item["name"] for item in inspector.get_columns("alerts")}

        signal_events = table("signal_events", column("id"), column("alert_source_id"))
        alerts = table("alerts", column("id"), column("alert_source_id"))
        alert_sources = table(
            "alert_sources", column("id"), column("management_type"), column("state")
        )
        with Session(mysql_engine) as session:
            assert set(session.scalars(select(alert_sources.c.id))) == {
                MANUAL_SYSTEM_SOURCE_ID,
                ALERTMANAGER_COMPAT_SOURCE_ID,
                CLOUDEVENTS_COMPAT_SOURCE_ID,
            }
            assert set(session.scalars(select(alert_sources.c.management_type))) == {
                "SYSTEM_MANAGED"
            }
            assert set(session.scalars(select(alert_sources.c.state))) == {"ENABLED"}
            assert (
                session.scalar(
                    select(signal_events.c.alert_source_id).where(signal_events.c.id == signal_id)
                )
                == MANUAL_SYSTEM_SOURCE_ID
            )
            assert (
                session.scalar(select(alerts.c.alert_source_id).where(alerts.c.id == alert_id))
                == MANUAL_SYSTEM_SOURCE_ID
            )
    finally:
        command.downgrade(alembic_config, "base")


def test_alert_source_migration_matches_orm_metadata(alembic_config: Config) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    try:
        command.check(alembic_config)
    finally:
        command.downgrade(alembic_config, "base")
