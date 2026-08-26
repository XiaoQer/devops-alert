from __future__ import annotations

from datetime import UTC, datetime

from alembic import command
from alembic.config import Config
from sqlalchemy import column, insert, inspect, select, table
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from incident_intelligence.domain.alert_sources import ALERTMANAGER_COMPAT_SOURCE_ID
from incident_intelligence.ids import new_id

NOW = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
GROUP_TABLES = {
    "alert_groups",
    "alert_group_members",
    "alert_grouping_jobs",
    "alert_group_correlation_jobs",
    "alert_group_decisions",
}


def _seed_alert_at_0005(engine: Engine) -> str:
    signal_id = new_id("sig")
    alert_id = new_id("alt")
    signal_events = table(
        "signal_events",
        column("id"),
        column("alert_source_id"),
        column("source"),
        column("source_event_id"),
        column("event_type"),
        column("title"),
        column("summary"),
        column("severity"),
        column("service"),
        column("environment"),
        column("observed_at"),
        column("received_at"),
        column("facts"),
        column("payload_fingerprint"),
        column("created_at"),
        column("version"),
    )
    alerts = table(
        "alerts",
        column("id"),
        column("signal_event_id"),
        column("alert_source_id"),
        column("source"),
        column("source_instance"),
        column("source_alert_key"),
        column("state"),
        column("title"),
        column("severity"),
        column("service"),
        column("environment"),
        column("first_observed_at"),
        column("last_observed_at"),
        column("state_changed_at"),
        column("created_at"),
        column("version"),
    )
    with Session(engine) as session:
        session.execute(
            insert(signal_events).values(
                id=signal_id,
                alert_source_id=ALERTMANAGER_COMPAT_SOURCE_ID,
                source="alertmanager",
                source_event_id="migration-signal",
                event_type="alert.firing",
                title="支付错误率升高",
                summary="支付错误率超过阈值",
                severity="high",
                service="payment-api",
                environment="production",
                observed_at=NOW,
                received_at=NOW,
                facts='{"symptom":"errors"}',
                payload_fingerprint="a" * 64,
                created_at=NOW,
                version=1,
            )
        )
        session.execute(
            insert(alerts).values(
                id=alert_id,
                signal_event_id=signal_id,
                alert_source_id=ALERTMANAGER_COMPAT_SOURCE_ID,
                source="alertmanager",
                source_instance="b" * 64,
                source_alert_key="payment-errors",
                state="ACTIVE",
                title="支付错误率升高",
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
    return alert_id


def test_upgrade_creates_alert_group_tables_and_backfills_alert_cycle(
    alembic_config: Config,
    mysql_engine: Engine,
) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "0005_alert_sources")
    alert_id = _seed_alert_at_0005(mysql_engine)
    try:
        command.upgrade(alembic_config, "head")

        inspector = inspect(mysql_engine)
        assert set(inspector.get_table_names()) >= GROUP_TABLES
        assert "cycle" in {item["name"] for item in inspector.get_columns("alerts")}
        alerts = table("alerts", column("id"), column("cycle"))
        with Session(mysql_engine) as session:
            assert session.scalar(select(alerts.c.cycle).where(alerts.c.id == alert_id)) == 1
    finally:
        command.downgrade(alembic_config, "base")


def test_alert_group_migration_matches_orm_and_downgrades_cleanly(
    alembic_config: Config,
    mysql_engine: Engine,
) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    command.check(alembic_config)
    command.downgrade(alembic_config, "0005_alert_sources")

    inspector = inspect(mysql_engine)
    assert GROUP_TABLES.isdisjoint(inspector.get_table_names())
    assert "cycle" not in {item["name"] for item in inspector.get_columns("alerts")}

    command.downgrade(alembic_config, "base")
