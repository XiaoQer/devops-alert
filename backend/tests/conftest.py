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
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from incident_intelligence.main import create_app
from incident_intelligence.settings import Settings


@pytest.fixture
def settings_factory() -> Callable[..., Settings]:
    def factory(**overrides: object) -> Settings:
        values: dict[str, object] = {
            "database_url": "mysql+pymysql://test-client@127.0.0.1/unused",
            "api_token": SecretStr(token_urlsafe(32)),
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
def postgres_engine() -> Iterator[Engine]:
    database_url = os.environ.get("II_TEST_DATABASE_URL")
    if database_url is None:
        pytest.fail("需要通过 II_TEST_DATABASE_URL 提供专用 PostgreSQL 测试库")

    parsed_url = make_url(database_url)
    if parsed_url.get_backend_name() != "postgresql":
        pytest.fail("II_TEST_DATABASE_URL 必须指向 PostgreSQL")

    schema_name = f"ii_test_{uuid4().hex}"
    bootstrap_engine = create_engine(database_url, pool_pre_ping=True)
    with bootstrap_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

    isolated_engine = create_engine(
        database_url,
        connect_args={"options": f"-csearch_path={schema_name}"},
        pool_pre_ping=True,
    )
    try:
        yield isolated_engine
    finally:
        isolated_engine.dispose()
        with bootstrap_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            connection.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
        bootstrap_engine.dispose()


@pytest.fixture
def alembic_config(postgres_engine: Engine) -> Config:
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    config.attributes["engine"] = postgres_engine
    return config


@pytest.fixture
def migrated_engine(alembic_config: Config, postgres_engine: Engine) -> Iterator[Engine]:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    try:
        yield postgres_engine
    finally:
        command.downgrade(alembic_config, "base")
