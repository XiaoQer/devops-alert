from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from incident_intelligence.ids import new_id
from incident_intelligence.persistence.models import AlertRow, SignalEventRow

NOW = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)


def make_signal(**overrides: object) -> SignalEventRow:
    values: dict[str, object] = {
        "id": new_id("sig"),
        "source": "manual",
        "source_event_id": "manual-001",
        "event_type": "manual.reported",
        "title": "支付接口错误率升高",
        "summary": "支付接口在生产环境持续返回错误",
        "severity": "high",
        "service": "payment-api",
        "environment": "production",
        "observed_at": NOW,
        "received_at": NOW,
        "facts": {"region": "cn-east-1"},
        "payload_fingerprint": "a" * 64,
        "created_at": NOW,
        "version": 1,
    }
    values.update(overrides)
    return SignalEventRow(**values)


def test_source_identity_is_unique(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(make_signal())
        session.commit()
        session.add(make_signal(id=new_id("sig")))

        with pytest.raises(IntegrityError):
            session.commit()

        session.rollback()
        assert session.scalar(select(func.count()).select_from(SignalEventRow)) == 1


def test_alert_rejects_incident_state_value(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        signal = make_signal()
        session.add(signal)
        session.flush()
        session.add(
            AlertRow(
                id=new_id("alt"),
                signal_event_id=signal.id,
                source="manual",
                source_instance="a" * 64,
                source_alert_key="b" * 64,
                state="DETECTED",
                title=signal.title,
                severity=signal.severity,
                service=signal.service,
                environment=signal.environment,
                first_observed_at=NOW,
                last_observed_at=NOW,
                state_changed_at=NOW,
                created_at=NOW,
                version=1,
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()

        session.rollback()


def test_alert_requires_existing_signal(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(
            AlertRow(
                id=new_id("alt"),
                signal_event_id=new_id("sig"),
                source="manual",
                source_instance="a" * 64,
                source_alert_key="b" * 64,
                state="ACTIVE",
                title="支付接口错误率升高",
                severity="high",
                service="payment-api",
                environment="production",
                first_observed_at=NOW,
                last_observed_at=NOW,
                state_changed_at=NOW,
                created_at=NOW,
                version=1,
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()

        session.rollback()


def test_signal_title_length_is_enforced_by_postgresql(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(make_signal(title="x" * 201))

        with pytest.raises(DataError):
            session.commit()

        session.rollback()
