from __future__ import annotations

from secrets import token_urlsafe

import pytest
from pydantic import SecretStr, ValidationError

from incident_intelligence.persistence.session import get_engine
from incident_intelligence.settings import Settings


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://test-client@db/app",
        "sqlite:///local.db",
        "mysql+mysqldb://test-client@db/app",
        "mysql+pymysql://test-client@db",
        "not-a-database-url",
    ],
)
def test_settings_rejects_non_pymysql_or_missing_database(database_url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=database_url, api_token=SecretStr(token_urlsafe(32)))


def test_settings_accepts_named_pymysql_database() -> None:
    settings = Settings(
        database_url="mysql+pymysql://test-client@127.0.0.1/incident_intelligence",
        api_token=SecretStr(token_urlsafe(32)),
    )

    assert settings.database_url == ("mysql+pymysql://test-client@127.0.0.1/incident_intelligence")


def test_get_engine_builds_mysql_engine_without_connecting() -> None:
    engine = get_engine("mysql+pymysql://test-client@127.0.0.1/incident_intelligence")

    assert engine.url.drivername == "mysql+pymysql"
    assert engine.dialect.name == "mysql"
    assert engine.pool._pre_ping is True  # type: ignore[attr-defined]
    engine.dispose()


def test_get_engine_rejects_non_mysql_url() -> None:
    with pytest.raises(ValueError, match=r"mysql\+pymysql"):
        get_engine("postgresql+psycopg://test-client@127.0.0.1/incident_intelligence")
