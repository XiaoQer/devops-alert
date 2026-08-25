from __future__ import annotations

import re

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

TEST_DATABASE_PATTERN = re.compile(r"^ii_test_[0-9a-f]{32}$")


def validate_test_database_name(name: str, expected: str) -> None:
    if name != expected or TEST_DATABASE_PATTERN.fullmatch(name) is None:
        raise ValueError("拒绝操作未通过精确校验的测试数据库")


def parse_mysql_test_bootstrap_url(value: str) -> URL:
    try:
        url = make_url(value)
    except ArgumentError as error:
        raise ValueError("需要指向 mysql 系统库的 MySQL 测试引导 URL") from error
    if url.drivername != "mysql+pymysql" or url.database != "mysql":
        raise ValueError("需要指向 mysql 系统库的 MySQL 测试引导 URL")
    return url
