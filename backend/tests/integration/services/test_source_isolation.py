from __future__ import annotations

from datetime import UTC, datetime
from functools import partial

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.signal_intake import SignalCommand
from incident_intelligence.persistence.models import AlertRow, DiagnosisRunRow, IncidentRow
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.signal_intake import SignalIntakeService

NOW = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)


@pytest.fixture
def session_factory(migrated_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def service(session_factory: sessionmaker[Session]) -> SignalIntakeService:
    return SignalIntakeService(
        uow_factory=partial(SqlAlchemyUnitOfWork, session_factory),
        clock=lambda: NOW,
    )


def _command(**overrides: object) -> SignalCommand:
    values: dict[str, object] = {
        "source": "cloudevents",
        "source_instance": "1" * 64,
        "source_event_id": "2" * 64,
        "source_alert_key": "shared-alert-key",
        "event_type": "alert.firing",
        "event_at": NOW,
        "episode_started_at": NOW,
        "title": "支付接口错误率升高",
        "summary": "支付接口错误率超过阈值",
        "severity": "high",
        "service": "payment-api",
        "environment": "production",
        "facts": {},
        "normalization_reason_codes": (),
    }
    values.update(overrides)
    return SignalCommand.model_validate(values)


def _count(session: Session, row_type: type[object]) -> int:
    return session.scalar(select(func.count()).select_from(row_type)) or 0


def test_same_alert_key_from_two_cloudevents_sources_creates_two_alerts(
    service: SignalIntakeService,
    session_factory: sessionmaker[Session],
) -> None:
    first = _command()
    second = _command(source_instance="3" * 64, source_event_id="4" * 64)

    first_result = service.submit_batch((first,), "cloudevents-adapter", "req-1")
    second_result = service.submit_batch((second,), "cloudevents-adapter", "req-2")

    assert first_result.items[0].alert_id != second_result.items[0].alert_id
    with session_factory() as session:
        assert _count(session, AlertRow) == 2
        assert _count(session, IncidentRow) == 0
        assert _count(session, DiagnosisRunRow) == 0


def test_same_key_and_instance_digest_from_different_adapter_types_do_not_collide(
    service: SignalIntakeService,
    session_factory: sessionmaker[Session],
) -> None:
    cloud = _command()
    alertmanager = _command(
        source="alertmanager",
        source_event_id="5" * 64,
    )

    cloud_result = service.submit_batch((cloud,), "cloudevents-adapter", "req-1")
    alertmanager_result = service.submit_batch((alertmanager,), "alertmanager-adapter", "req-2")

    assert cloud_result.items[0].alert_id != alertmanager_result.items[0].alert_id
    with session_factory() as session:
        sources = set(session.scalars(select(AlertRow.source)))
        assert sources == {"cloudevents", "alertmanager"}
        assert _count(session, AlertRow) == 2
