"""共享外部信号接入服务的真实 MySQL 集成测试。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial
from threading import Barrier

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.enums import AlertState
from incident_intelligence.domain.forbidden_identity import ForbiddenIdentityError
from incident_intelligence.domain.signal_intake import SignalCommand
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.models import (
    AlertRow,
    AuditEventRow,
    DiagnosisRunRow,
    IncidentRow,
    SignalEventRow,
    SignalIntakeResultRow,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.signal_intake import (
    SignalIntakeBatchResult,
    SignalIntakeService,
    SourceEventConflict,
)

NOW = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)
TIME_2 = NOW + timedelta(minutes=5)
TIME_3 = TIME_2 + timedelta(minutes=5)
TIME_4 = TIME_3 + timedelta(minutes=5)


def firing_command(**overrides: object) -> SignalCommand:
    values: dict[str, object] = {
        "source": "alertmanager",
        "source_instance": "1" * 64,
        "source_event_id": "2" * 64,
        "source_alert_key": "payment-high-error-rate",
        "event_type": "alert.firing",
        "event_at": NOW,
        "episode_started_at": NOW,
        "title": "支付接口错误率升高",
        "summary": "支付接口错误率超过阈值",
        "severity": "high",
        "service": "payment-api",
        "environment": "production",
        "facts": {"region": "cn-east-1"},
        "normalization_reason_codes": (),
    }
    values.update(overrides)
    return SignalCommand.model_validate(values)


def resolved_command(**overrides: object) -> SignalCommand:
    values: dict[str, object] = {
        **firing_command().model_dump(),
        "source_event_id": "3" * 64,
        "event_type": "alert.resolved",
    }
    values.update(overrides)
    return SignalCommand.model_validate(values)


@pytest.fixture
def session_factory(migrated_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def service(session_factory: sessionmaker[Session]) -> SignalIntakeService:
    return SignalIntakeService(
        uow_factory=partial(SqlAlchemyUnitOfWork, session_factory),
        clock=lambda: NOW,
    )


def count_rows(session: Session, row_type: type[object]) -> int:
    return session.scalar(select(func.count()).select_from(row_type)) or 0


def test_firing_creates_only_signal_alert_result_and_audits(
    service: SignalIntakeService,
    session_factory: sessionmaker[Session],
) -> None:
    command = firing_command()
    result = service.submit_batch([command], "alertmanager", "req-1")

    with session_factory() as session:
        assert count_rows(session, SignalEventRow) == 1
        assert count_rows(session, AlertRow) == 1
        assert count_rows(session, SignalIntakeResultRow) == 1
        assert count_rows(session, AuditEventRow) == 2
        assert count_rows(session, IncidentRow) == 0
        assert count_rows(session, DiagnosisRunRow) == 0
        audits = list(session.scalars(select(AuditEventRow).order_by(AuditEventRow.action)))
        assert {audit.action for audit in audits} == {"alert.opened", "signal.received"}
        assert all(
            set(audit.details) <= {"reason_code", "adapter", "parent_id"} for audit in audits
        )
        serialized_details = " ".join(str(audit.details) for audit in audits)
        assert command.title not in serialized_details
        assert command.summary not in serialized_details
    assert result.items[0].outcome == "opened"
    assert result.items[0].replayed is False
    assert result.counts.opened == 1


def test_projection_sequence_updates_resolves_ignores_stale_and_reopens(
    service: SignalIntakeService,
    session_factory: sessionmaker[Session],
) -> None:
    commands = (
        firing_command(event_at=NOW, episode_started_at=NOW),
        firing_command(source_event_id="4" * 64, event_at=TIME_2, episode_started_at=NOW),
        resolved_command(source_event_id="5" * 64, event_at=TIME_3),
        firing_command(source_event_id="6" * 64, event_at=TIME_2, episode_started_at=NOW),
        firing_command(source_event_id="7" * 64, event_at=TIME_4, episode_started_at=TIME_4),
    )

    outcomes = [
        service.submit_batch([command], "alertmanager", f"req-{index}").items[0].outcome
        for index, command in enumerate(commands, start=1)
    ]

    assert outcomes == ["opened", "updated", "resolved", "stale", "reopened"]
    with session_factory() as session:
        alert = session.scalar(select(AlertRow))
        assert alert is not None
        assert alert.state == AlertState.ACTIVE.value
        assert alert.signal_event_id.startswith("sig_")
        assert alert.first_observed_at == TIME_4
        assert alert.last_observed_at == TIME_4
        assert alert.version == 4
        assert count_rows(session, SignalEventRow) == 5
        assert count_rows(session, AlertRow) == 1
        assert count_rows(session, SignalIntakeResultRow) == 5
        assert count_rows(session, AuditEventRow) == 10


def test_exact_command_replays_original_result_without_new_rows_or_audit(
    service: SignalIntakeService,
    session_factory: sessionmaker[Session],
) -> None:
    command = firing_command()

    first = service.submit_batch([command], "alertmanager", "req-1")
    replay = service.submit_batch([command], "alertmanager", "req-2")

    assert replay.items[0].model_copy(update={"replayed": False}) == first.items[0]
    assert replay.items[0].replayed is True
    assert replay.counts.replayed == 1
    with session_factory() as session:
        assert count_rows(session, SignalEventRow) == 1
        assert count_rows(session, AlertRow) == 1
        assert count_rows(session, SignalIntakeResultRow) == 1
        assert count_rows(session, AuditEventRow) == 2


def test_same_source_event_identity_with_different_content_conflicts(
    service: SignalIntakeService,
    session_factory: sessionmaker[Session],
) -> None:
    command = firing_command()
    service.submit_batch([command], "alertmanager", "req-1")

    with pytest.raises(SourceEventConflict) as error:
        service.submit_batch(
            [command.model_copy(update={"title": "另一条规范化标题"})],
            "alertmanager",
            "req-2",
        )

    assert error.value.reason_code == "source_event_conflict"
    with session_factory() as session:
        assert count_rows(session, SignalEventRow) == 1
        assert count_rows(session, AuditEventRow) == 2


def test_orphan_resolved_replay_keeps_original_null_alert_id(
    service: SignalIntakeService,
    session_factory: sessionmaker[Session],
) -> None:
    orphan = resolved_command(source="cloudevents")
    first = service.submit_batch([orphan], "cloudevents", "req-1")
    service.submit_batch(
        [
            firing_command(
                source="cloudevents",
                source_event_id="8" * 64,
                event_at=TIME_2,
                episode_started_at=TIME_2,
            )
        ],
        "cloudevents",
        "req-2",
    )
    replay = service.submit_batch([orphan], "cloudevents", "req-3")

    assert first.items[0].outcome == "orphan_resolved"
    assert first.items[0].alert_id is None
    assert replay.items[0].alert_id is None
    assert replay.items[0].replayed is True
    with session_factory() as session:
        assert count_rows(session, SignalEventRow) == 2
        assert count_rows(session, AlertRow) == 1
        assert count_rows(session, AuditEventRow) == 4


def test_forbidden_identity_is_rejected_before_any_batch_write(
    service: SignalIntakeService,
    session_factory: sessionmaker[Session],
) -> None:
    command = firing_command(facts={"scenario_id": "hidden"})

    with pytest.raises(ForbiddenIdentityError):
        service.submit_batch([command], "alertmanager", "req-1")

    with session_factory() as session:
        assert count_rows(session, SignalEventRow) == 0
        assert count_rows(session, SignalIntakeResultRow) == 0
        assert count_rows(session, AuditEventRow) == 0


def test_concurrent_duplicate_converges_to_one_result_and_one_audit_pair(
    session_factory: sessionmaker[Session],
) -> None:
    command = firing_command()
    simultaneous_writes = Barrier(2)

    def synchronized_clock() -> datetime:
        simultaneous_writes.wait(timeout=5)
        return NOW

    service = SignalIntakeService(
        uow_factory=partial(SqlAlchemyUnitOfWork, session_factory),
        clock=synchronized_clock,
    )

    def submit(request_id: str) -> SignalIntakeBatchResult:
        return service.submit_batch([command], "alertmanager", request_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, ["req-1", "req-2"]))

    assert {result.items[0].replayed for result in results} == {False, True}
    assert len({result.items[0].signal_event_id for result in results}) == 1
    with session_factory() as session:
        assert count_rows(session, SignalEventRow) == 1
        assert count_rows(session, AlertRow) == 1
        assert count_rows(session, SignalIntakeResultRow) == 1
        assert count_rows(session, AuditEventRow) == 2


def test_second_command_failure_rolls_back_entire_batch(
    session_factory: sessionmaker[Session],
) -> None:
    signal_count = 0

    def fail_second_signal(prefix: IdPrefix) -> str:
        nonlocal signal_count
        if prefix == "sig":
            signal_count += 1
            if signal_count == 2:
                return "sig_invalid"
        return new_id(prefix)

    service = SignalIntakeService(
        uow_factory=partial(SqlAlchemyUnitOfWork, session_factory),
        clock=lambda: NOW,
        id_factory=fail_second_signal,
    )
    commands = [
        firing_command(),
        firing_command(source_event_id="9" * 64, source_alert_key="inventory-high-latency"),
    ]

    with pytest.raises(ValidationError):
        service.submit_batch(commands, "alertmanager", "req-1")

    with session_factory() as session:
        assert count_rows(session, SignalEventRow) == 0
        assert count_rows(session, AlertRow) == 0
        assert count_rows(session, SignalIntakeResultRow) == 0
        assert count_rows(session, AuditEventRow) == 0


def test_batch_preserves_input_order_for_replay_and_new_event(
    service: SignalIntakeService,
) -> None:
    existing = firing_command()
    first = service.submit_batch([existing], "alertmanager", "req-1")
    new_command = firing_command(
        source_event_id="a" * 64,
        source_alert_key="inventory-high-latency",
    )

    batch = service.submit_batch([existing, new_command], "alertmanager", "req-2")

    assert batch.items[0].signal_event_id == first.items[0].signal_event_id
    assert batch.items[0].replayed is True
    assert batch.items[1].signal_event_id != first.items[0].signal_event_id
    assert batch.items[1].replayed is False
    assert batch.counts.replayed == 1
    assert batch.counts.opened == 1
