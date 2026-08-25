from __future__ import annotations

import pytest

from tests.support.database import (
    parse_mysql_test_bootstrap_url,
    validate_test_database_name,
)

EXPECTED_NAME = "ii_test_" + "a" * 32


@pytest.mark.parametrize(
    "database_name",
    [
        "incident_intelligence",
        "ii_test_",
        "ii_test_nothex",
        "ii_test_" + "b" * 32,
        "II_TEST_" + "a" * 32,
    ],
)
def test_database_guard_rejects_unsafe_or_different_name(database_name: str) -> None:
    with pytest.raises(ValueError, match="测试数据库"):
        validate_test_database_name(database_name, EXPECTED_NAME)


def test_database_guard_accepts_only_exact_generated_name() -> None:
    validate_test_database_name(EXPECTED_NAME, EXPECTED_NAME)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://test-client@127.0.0.1/mysql",
        "sqlite:///mysql",
        "mysql+mysqldb://test-client@127.0.0.1/mysql",
        "mysql+pymysql://test-client@127.0.0.1/incident_intelligence",
        "mysql+pymysql://test-client@127.0.0.1",
        "not-a-database-url",
    ],
)
def test_test_bootstrap_url_rejects_unsafe_target(database_url: str) -> None:
    with pytest.raises(ValueError, match="MySQL 测试引导"):
        parse_mysql_test_bootstrap_url(database_url)


def test_test_bootstrap_url_accepts_mysql_system_database() -> None:
    url = parse_mysql_test_bootstrap_url("mysql+pymysql://test-client@127.0.0.1:43306/mysql")

    assert url.drivername == "mysql+pymysql"
    assert url.database == "mysql"
