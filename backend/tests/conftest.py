from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.engine import Engine

from incident_intelligence.main import create_app
from incident_intelligence.persistence.session import get_engine
from incident_intelligence.settings import Settings
from tests.support.database import (
    parse_mysql_test_bootstrap_url,
    validate_test_database_name,
)


@pytest.fixture
def settings_factory() -> Callable[..., Settings]:
    def factory(**overrides: object) -> Settings:
        values: dict[str, object] = {
            "database_url": "mysql+pymysql://test-client@127.0.0.1/unused",
            "api_token": SecretStr(token_urlsafe(32)),
            "alertmanager_token": SecretStr(token_urlsafe(32)),
            "cloudevents_token": SecretStr(token_urlsafe(32)),
            "correlation_runner_enabled": False,
            "alert_grouping_runner_enabled": False,
        }
        values.update(overrides)
        return Settings(**values)

    return factory


@pytest.fixture
def client(settings_factory: Callable[..., Settings]) -> Iterator[TestClient]:
    app = create_app(settings_factory())
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def mysql_engine() -> Iterator[Engine]:
    database_url = os.environ.get("II_TEST_DATABASE_URL")
    if database_url is None:
        pytest.fail("需要通过 II_TEST_DATABASE_URL 提供 MySQL 测试引导库")

    try:
        parsed_url = parse_mysql_test_bootstrap_url(database_url)
    except ValueError as error:
        pytest.fail(str(error))

    database_name = f"ii_test_{uuid4().hex}"
    bootstrap_engine = get_engine(database_url)
    isolated_engine: Engine | None = None
    database_created = False
    try:
        with bootstrap_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            connection.exec_driver_sql(
                f"CREATE DATABASE `{database_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
            )
        database_created = True
        isolated_url = parsed_url.set(database=database_name)
        isolated_engine = get_engine(isolated_url.render_as_string(hide_password=False))
        yield isolated_engine
    finally:
        if isolated_engine is not None:
            isolated_engine.dispose()
        if database_created:
            validate_test_database_name(database_name, database_name)
            with bootstrap_engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as connection:
                connection.exec_driver_sql(f"DROP DATABASE `{database_name}`")
        bootstrap_engine.dispose()


@pytest.fixture
def alembic_config(mysql_engine: Engine) -> Config:
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    config.attributes["engine"] = mysql_engine
    return config


@pytest.fixture
def migrated_engine(alembic_config: Config, mysql_engine: Engine) -> Iterator[Engine]:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    try:
        yield mysql_engine
    finally:
        command.downgrade(alembic_config, "base")
