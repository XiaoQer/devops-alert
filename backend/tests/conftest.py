from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from secrets import token_urlsafe
from uuid import uuid4

import pytest
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
            "database_url": "postgresql+psycopg://unused",
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
    engine = create_engine(database_url, pool_pre_ping=True)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

    isolated_engine = engine.execution_options(schema_translate_map={None: schema_name})
    try:
        yield isolated_engine
    finally:
        isolated_engine.dispose()
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
        engine.dispose()
