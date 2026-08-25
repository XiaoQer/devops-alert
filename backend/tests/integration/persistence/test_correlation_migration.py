from __future__ import annotations

from datetime import UTC, datetime

from alembic import command
from alembic.config import Config
from sqlalchemy import column, func, insert, inspect, select, table
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from incident_intelligence.ids import new_id
from incident_intelligence.persistence.models import (
    AlertRow,
    IncidentAlertLinkRow,
    IncidentRow,
    SignalEventRow,
)

NOW = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)
BASELINE_TABLES = {
    "signal_events",
    "alerts",
    "incidents",
    "diagnosis_runs",
    "ingestion_keys",
    "audit_events",
    "signal_intake_results",
}
CORRELATION_TABLES = {
    "service_catalog_entries",
    "service_catalog_state",
    "service_dependencies",
    "correlation_jobs",
    "correlation_decisions",
    "incident_alert_links",
}


def _seed_manual_incident(engine: Engine) -> tuple[str, str]:
    signal_id = new_id("sig")
    alert_id = new_id("alt")
    incident_id = new_id("inc")
    with Session(engine) as session:
        session.add(
            SignalEventRow(
                id=signal_id,
                source="manual",
                source_event_id="manual-correlation-migration",
                event_type="manual.reported",
                title="支付接口错误率升高",
                summary="支付接口持续返回错误",
                severity="high",
                service="payment-api",
                environment="production",
                observed_at=NOW,
                received_at=NOW,
                facts={"region": "cn-east-1"},
                payload_fingerprint="a" * 64,
                created_at=NOW,
                version=1,
            )
        )
        session.flush()
        session.add(
            AlertRow(
                id=alert_id,
                signal_event_id=signal_id,
                source="manual",
                source_instance="b" * 64,
                source_alert_key="manual-correlation-migration",
                state="ACTIVE",
                title="支付接口错误率升高",
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
        session.flush()
        historical_incidents = table(
            "incidents",
            column("id"),
            column("primary_alert_id"),
            column("state"),
            column("title"),
            column("severity"),
            column("service"),
            column("environment"),
            column("detected_at"),
            column("created_at"),
            column("version"),
        )
        session.execute(
            insert(historical_incidents).values(
                id=incident_id,
                primary_alert_id=alert_id,
                state="DETECTED",
                title="支付接口错误率升高",
                severity="high",
                service="payment-api",
                environment="production",
                detected_at=NOW,
                created_at=NOW,
                version=1,
            )
        )
        session.commit()
    return alert_id, incident_id


def test_upgrade_adds_correlation_tables_and_backfills_primary_links(
    alembic_config: Config,
    mysql_engine: Engine,
) -> None:
    command.upgrade(alembic_config, "0001_mysql_initial")
    alert_id, incident_id = _seed_manual_incident(mysql_engine)
    try:
        command.upgrade(alembic_config, "head")

        inspector = inspect(mysql_engine)
        assert set(inspector.get_table_names()) >= CORRELATION_TABLES
        for table_name in CORRELATION_TABLES:
            assert inspector.get_table_options(table_name)["mysql_engine"] == "InnoDB"
        columns = {
            column["name"]: column["type"]
            for column in inspector.get_columns("correlation_decisions")
        }
        assert isinstance(columns["facts"], mysql.JSON)
        assert isinstance(columns["created_at"], mysql.DATETIME)
        assert columns["created_at"].fsp == 6

        with Session(mysql_engine) as session:
            link = session.scalar(select(IncidentAlertLinkRow))
            assert link is not None
            assert link.alert_id == alert_id
            assert link.incident_id == incident_id
            assert link.relation == "PRIMARY"
            assert link.decision_id is None
            assert link.linked_at == NOW
            assert link.created_at == NOW
    finally:
        command.downgrade(alembic_config, "base")


def test_downgrade_to_baseline_preserves_existing_incident_data(
    alembic_config: Config,
    mysql_engine: Engine,
) -> None:
    command.upgrade(alembic_config, "0001_mysql_initial")
    _seed_manual_incident(mysql_engine)
    command.upgrade(alembic_config, "head")
    try:
        command.downgrade(alembic_config, "0001_mysql_initial")

        table_names = set(inspect(mysql_engine).get_table_names())
        assert table_names >= BASELINE_TABLES
        assert CORRELATION_TABLES.isdisjoint(table_names)
        with Session(mysql_engine) as session:
            assert session.scalar(select(func.count()).select_from(IncidentRow)) == 1
    finally:
        command.downgrade(alembic_config, "base")


def test_correlation_migration_matches_orm_metadata(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    try:
        command.check(alembic_config)
    finally:
        command.downgrade(alembic_config, "base")


def test_assignment_migration_adds_nullable_pair(
    alembic_config: Config,
    mysql_engine: Engine,
) -> None:
    command.upgrade(alembic_config, "head")
    try:
        columns = {
            column["name"]: column for column in inspect(mysql_engine).get_columns("incidents")
        }
        assert columns["assignee"]["nullable"] is True
        assert columns["claimed_at"]["nullable"] is True
        command.check(alembic_config)
    finally:
        command.downgrade(alembic_config, "base")
