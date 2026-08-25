from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import mysql

from incident_intelligence.persistence.types import UtcDateTime


def test_utc_datetime_stores_naive_utc_and_restores_aware_utc() -> None:
    column_type = UtcDateTime()
    source = datetime(
        2026,
        8,
        25,
        16,
        0,
        0,
        123456,
        tzinfo=timezone(timedelta(hours=8)),
    )

    stored = column_type.process_bind_param(source, mysql.dialect())
    restored = column_type.process_result_value(stored, mysql.dialect())

    assert stored == datetime(2026, 8, 25, 8, 0, 0, 123456)
    assert restored == datetime(2026, 8, 25, 8, 0, 0, 123456, tzinfo=UTC)


def test_utc_datetime_rejects_naive_value() -> None:
    with pytest.raises(ValueError, match="UTC"):
        UtcDateTime().process_bind_param(datetime(2026, 8, 25, 8, 0), mysql.dialect())


def test_utc_datetime_preserves_none() -> None:
    column_type = UtcDateTime()

    assert column_type.process_bind_param(None, mysql.dialect()) is None
    assert column_type.process_result_value(None, mysql.dialect()) is None
