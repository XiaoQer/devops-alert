from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from incident_intelligence.ids import new_id
from incident_intelligence.persistence.models import SignalEventRow

NOW = datetime(2026, 8, 25, 8, 0, 0, 123456, tzinfo=UTC)


def make_signal(**overrides: object) -> SignalEventRow:
    values: dict[str, object] = {
        "id": new_id("sig"),
        "source": "cloudevents",
        "source_event_id": "Event-A",
        "event_type": "alert.firing",
        "title": "支付接口错误率升高",
        "summary": "支付接口在生产环境持续返回错误",
        "severity": "high",
        "service": "payment-api",
        "environment": "production",
        "observed_at": NOW,
        "received_at": NOW,
        "facts": {"region": "华东"},
        "payload_fingerprint": "a" * 64,
        "created_at": NOW,
        "version": 1,
    }
    values.update(overrides)
    return SignalEventRow(**values)


def test_mysql_json_and_utc_microseconds_round_trip(migrated_engine: Engine) -> None:
    signal = make_signal()
    with Session(migrated_engine) as session:
        session.add(signal)
        session.commit()
        session.expire_all()
        restored = session.get(SignalEventRow, signal.id)

    assert restored is not None
    assert restored.facts == {"region": "华东"}
    assert restored.observed_at == NOW
    assert restored.observed_at.tzinfo is UTC


def test_source_identity_is_case_sensitive(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        session.add_all(
            [
                make_signal(source_event_id="Event-A"),
                make_signal(id=new_id("sig"), source_event_id="event-a"),
            ]
        )
        session.commit()

        assert session.scalar(select(func.count()).select_from(SignalEventRow)) == 2


def test_exact_source_identity_remains_unique(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(make_signal(source_event_id="event-a"))
        session.commit()
        session.add(make_signal(id=new_id("sig"), source_event_id="event-a"))

        with pytest.raises(IntegrityError):
            session.commit()
