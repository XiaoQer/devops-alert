from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import Engine

EXPECTED_TABLES = {
    "signal_events",
    "alerts",
    "incidents",
    "diagnosis_runs",
    "ingestion_keys",
    "audit_events",
    "signal_intake_results",
}


def test_upgrade_creates_all_initial_domain_tables(
    alembic_config: Config, mysql_engine: Engine
) -> None:
    command.upgrade(alembic_config, "head")

    inspector = inspect(mysql_engine)
    assert set(inspector.get_table_names()) >= EXPECTED_TABLES
    assert inspector.get_table_options("signal_events")["mysql_engine"] == "InnoDB"
    columns = {column["name"]: column["type"] for column in inspector.get_columns("signal_events")}
    assert isinstance(columns["facts"], mysql.JSON)
    assert isinstance(columns["observed_at"], mysql.DATETIME)
    assert columns["observed_at"].fsp == 6

    command.downgrade(alembic_config, "base")


def test_downgrade_removes_initial_domain_tables(
    alembic_config: Config, mysql_engine: Engine
) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")

    assert EXPECTED_TABLES.isdisjoint(inspect(mysql_engine).get_table_names())


def test_migration_matches_orm_metadata(alembic_config: Config) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    try:
        command.check(alembic_config)
    finally:
        command.downgrade(alembic_config, "base")
