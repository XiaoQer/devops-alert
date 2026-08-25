from __future__ import annotations

import re

from sqlalchemy.engine import Engine


def test_mysql_engine_uses_isolated_database_and_required_session(
    mysql_engine: Engine,
) -> None:
    with mysql_engine.connect() as connection:
        database_name, isolation, time_zone, charset = connection.exec_driver_sql(
            "SELECT DATABASE(), @@transaction_isolation, @@session.time_zone, "
            "@@character_set_connection"
        ).one()

    assert re.fullmatch(r"ii_test_[0-9a-f]{32}", database_name)
    assert isolation == "READ-COMMITTED"
    assert time_zone == "+00:00"
    assert charset == "utf8mb4"
