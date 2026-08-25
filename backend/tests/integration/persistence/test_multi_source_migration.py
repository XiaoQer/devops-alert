from __future__ import annotations

from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DataError, IntegrityError

NOW = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)


def insert_legacy_manual_record(engine: Engine, source_event_id: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO signal_events "
                "(id, source, source_event_id, title, summary, severity, service, environment, "
                "observed_at, received_at, facts, payload_fingerprint, created_at, version) "
                "VALUES (:id, 'manual', :source_event_id, '接口错误率升高', '接口持续返回错误', "
                "'high', 'payment-api', 'production', :now, :now, '{}'::jsonb, "
                ":fingerprint, :now, 1)"
            ),
            {
                "id": "sig_" + "1" * 32,
                "source_event_id": source_event_id,
                "now": NOW,
                "fingerprint": "a" * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO alerts "
                "(id, signal_event_id, state, title, severity, service, environment, "
                "first_observed_at, last_observed_at, created_at, version) "
                "VALUES (:id, :signal_id, 'ACTIVE', '接口错误率升高', 'high', 'payment-api', "
                "'production', :now, :now, :now, 1)"
            ),
            {"id": "alt_" + "2" * 32, "signal_id": "sig_" + "1" * 32, "now": NOW},
        )


def test_existing_manual_rows_are_backfilled_without_plain_idempotency_key(
    alembic_config: Config, mysql_engine: Engine
) -> None:
    command.upgrade(alembic_config, "0001_initial_domain")
    insert_legacy_manual_record(mysql_engine, source_event_id="private-key-1")

    command.upgrade(alembic_config, "head")

    with mysql_engine.connect() as connection:
        signal = connection.execute(text("SELECT event_type FROM signal_events")).one()
        alert = connection.execute(
            text("SELECT source, source_instance, source_alert_key, state_changed_at FROM alerts")
        ).one()
    assert signal.event_type == "manual.reported"
    assert alert.source == "manual"
    assert len(alert.source_instance) == 64
    assert len(alert.source_alert_key) == 64
    assert "private-key-1" not in (alert.source_instance, alert.source_alert_key)
    assert alert.state_changed_at == NOW

    command.downgrade(alembic_config, "base")


def test_alert_source_identity_and_event_type_are_database_constraints(
    alembic_config: Config, mysql_engine: Engine
) -> None:
    command.upgrade(alembic_config, "head")
    columns = {item["name"] for item in inspect(mysql_engine).get_columns("alerts")}
    assert {"source", "source_instance", "source_alert_key", "state_changed_at"} <= columns

    with (
        mysql_engine.begin() as connection,
        pytest.raises((IntegrityError, DataError)),
    ):
        connection.execute(
            text(
                "INSERT INTO signal_events "
                "(id, source, source_event_id, event_type, title, summary, severity, service, "
                "environment, observed_at, received_at, facts, payload_fingerprint, "
                "created_at, version) "
                "VALUES (:id, 'manual', 'bad-event', 'experiment.started', '标题', '摘要', "
                "'high', 'svc', 'production', :now, :now, '{}'::jsonb, :fingerprint, :now, 1)"
            ),
            {"id": "sig_" + "3" * 32, "now": NOW, "fingerprint": "b" * 64},
        )

    command.downgrade(alembic_config, "base")


def test_signal_intake_results_enforces_fixed_outcomes_and_fingerprints(
    alembic_config: Config, mysql_engine: Engine
) -> None:
    command.upgrade(alembic_config, "head")

    with (
        mysql_engine.begin() as connection,
        pytest.raises((IntegrityError, DataError)),
    ):
        connection.execute(
            text(
                "INSERT INTO signal_intake_results "
                "(source, source_event_id, command_fingerprint, signal_event_id, alert_id, "
                "outcome, created_at) "
                "VALUES ('cloudevents', :event_id, :fingerprint, :signal_id, NULL, "
                "'invented', :now)"
            ),
            {
                "event_id": "c" * 64,
                "fingerprint": "too-short",
                "signal_id": "sig_" + "4" * 32,
                "now": NOW,
            },
        )

    command.downgrade(alembic_config, "base")
