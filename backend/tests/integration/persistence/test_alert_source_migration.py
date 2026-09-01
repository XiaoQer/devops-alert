from __future__ import annotations

from datetime import UTC, datetime

import pytest
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
            "environment",
            "environment_name",
            "environment_configured",
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
            "alert_sources",
            column("id"),
            column("management_type"),
            column("state"),
            column("environment"),
            column("environment_name"),
            column("environment_configured"),
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
            assert set(session.scalars(select(alert_sources.c.environment))) == {"unknown"}
            assert set(session.scalars(select(alert_sources.c.environment_name))) == {"环境待配置"}
            assert set(session.scalars(select(alert_sources.c.environment_configured))) == {False}
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


def test_upgrade_restores_missing_system_sources_without_overwriting_user_sources(
    alembic_config: Config,
    mysql_engine: Engine,
) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "0017_feishu_activity_text")
    alert_sources = table(
        "alert_sources",
        column("id"),
        column("name"),
        column("source_type"),
        column("management_type"),
        column("state"),
        column("environment"),
        column("environment_name"),
        column("environment_configured"),
        column("version"),
        column("accepted_requests"),
        column("rejected_requests"),
        column("opened_count"),
        column("updated_count"),
        column("resolved_count"),
        column("replayed_count"),
        column("ignored_count"),
        column("created_at"),
        column("updated_at"),
    )
    user_source_id = new_id("src")
    try:
        with Session(mysql_engine) as session:
            session.execute(
                alert_sources.delete().where(
                    alert_sources.c.id.in_(
                        (
                            MANUAL_SYSTEM_SOURCE_ID,
                            ALERTMANAGER_COMPAT_SOURCE_ID,
                            CLOUDEVENTS_COMPAT_SOURCE_ID,
                        )
                    )
                )
            )
            session.execute(
                insert(alert_sources).values(
                    id=user_source_id,
                    name="Alertmanager 兼容接入",
                    source_type="ALERTMANAGER",
                    management_type="USER_MANAGED",
                    state="ENABLED",
                    environment="production",
                    environment_name="生产环境",
                    environment_configured=True,
                    version=3,
                    accepted_requests=7,
                    rejected_requests=0,
                    opened_count=7,
                    updated_count=0,
                    resolved_count=0,
                    replayed_count=0,
                    ignored_count=0,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.commit()

        command.upgrade(alembic_config, "head")

        with Session(mysql_engine) as session:
            sources = {row["id"]: row for row in session.execute(select(alert_sources)).mappings()}
            assert set(sources) == {
                MANUAL_SYSTEM_SOURCE_ID,
                ALERTMANAGER_COMPAT_SOURCE_ID,
                CLOUDEVENTS_COMPAT_SOURCE_ID,
                user_source_id,
            }
            assert sources[ALERTMANAGER_COMPAT_SOURCE_ID]["name"].startswith(
                "Alertmanager 兼容接入"
            )
            assert sources[ALERTMANAGER_COMPAT_SOURCE_ID]["name"] != sources[user_source_id]["name"]
            assert sources[ALERTMANAGER_COMPAT_SOURCE_ID]["environment_configured"] == 0
            assert sources[user_source_id]["name"] == "Alertmanager 兼容接入"
            assert sources[user_source_id]["version"] == 3
            assert sources[user_source_id]["accepted_requests"] == 7
    finally:
        command.downgrade(alembic_config, "base")


def test_downgrade_rejects_custom_environments_without_losing_data(
    alembic_config: Config,
    mysql_engine: Engine,
) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    service_catalog = table(
        "service_catalog_entries",
        column("id"),
        column("service"),
        column("environment"),
        column("owner_team"),
        column("state"),
        column("created_at"),
        column("updated_at"),
        column("version"),
    )
    service_id = new_id("svc")
    try:
        with Session(mysql_engine) as session:
            session.execute(
                insert(service_catalog).values(
                    id=service_id,
                    service="payment-api",
                    environment="private",
                    owner_team="payments",
                    state="ACTIVE",
                    created_at=NOW,
                    updated_at=NOW,
                    version=1,
                )
            )
            session.commit()

        with pytest.raises(RuntimeError, match="旧版本不支持自定义环境"):
            command.downgrade(alembic_config, "0008_problem_signature_grouping")

        with Session(mysql_engine) as session:
            assert (
                session.scalar(
                    select(service_catalog.c.environment).where(service_catalog.c.id == service_id)
                )
                == "private"
            )
            session.execute(service_catalog.delete().where(service_catalog.c.id == service_id))
            session.commit()
    finally:
        command.downgrade(alembic_config, "base")
