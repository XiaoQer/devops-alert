from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial
from threading import Barrier, Lock

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.signal_intake import SignalCommand
from incident_intelligence.persistence.alert_lifecycle_repository import AlertLifecycleRepository
from incident_intelligence.persistence.incident_repository import (
    IncidentEvaluationJobRepository,
)
from incident_intelligence.persistence.models import (
    AlertGroupingJobRow,
    AlertLifecycleRow,
    AlertRow,
    AuditEventRow,
    IncidentEvaluationJobRow,
    SignalEventRow,
    SignalIntakeResultRow,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.signal_intake import SignalIntakeService, SourceEventConflict

NOW = datetime(2026, 8, 28, 8, 0, tzinfo=UTC)
SOURCE_ID = "src_00000000000000000000000000000002"


def command(**overrides: object) -> SignalCommand:
    values: dict[str, object] = {
        "alert_source_id": SOURCE_ID,
        "source": "alertmanager",
        "source_instance": "1" * 64,
        "source_event_id": "2" * 64,
        "source_alert_key": "mysql-lock-wait",
        "event_type": "alert.firing",
        "event_at": NOW,
        "episode_started_at": NOW,
        "alert_name": "MySQLRowLockWaitActive",
        "title": "MySQLRowLockWaitActive",
        "summary": "当前检测到活跃行锁等待",
        "description": "等待事务超过阈值",
        "severity": "high",
        "service": None,
        "environment": "production",
        "facts": {"instance": "mysql:3306"},
        "normalization_reason_codes": (),
    }
    values.update(overrides)
    return SignalCommand.model_validate(values)


@pytest.fixture
def session_factory(migrated_engine) -> sessionmaker[Session]:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def service(session_factory: sessionmaker[Session]) -> SignalIntakeService:
    return SignalIntakeService(
        uow_factory=partial(SqlAlchemyUnitOfWork, session_factory),
        clock=lambda: NOW.replace(hour=10),
    )


def count(session: Session, row_type: type[object]) -> int:
    return session.scalar(select(func.count()).select_from(row_type)) or 0


def test_first_receive_creates_signal_and_active_alert_lifecycle(
    service: SignalIntakeService, session_factory: sessionmaker[Session]
) -> None:
    result = service.submit_batch([command()], "alertmanager", "req-1")
    with session_factory() as session:
        assert count(session, SignalEventRow) == 1
        assert count(session, SignalIntakeResultRow) == 1
        assert count(session, AuditEventRow) == 2
        assert count(session, AlertLifecycleRow) == 1
        assert count(session, IncidentEvaluationJobRow) == 1
        assert count(session, AlertRow) == 0
        assert count(session, AlertGroupingJobRow) == 0
        job = session.scalar(select(IncidentEvaluationJobRow))
        assert job is not None
        assert job.alert_id == result.items[0].alert_id
        assert job.alert_version == 1
        assert job.state == "PENDING"
    assert result.items[0].signal_event_id.startswith("sig_")
    assert result.items[0].alert_id is not None
    assert result.items[0].alert_id.startswith("alt_")


def test_exact_replay_returns_original_signal_without_second_record(
    service: SignalIntakeService, session_factory: sessionmaker[Session]
) -> None:
    first = service.submit_batch([command()], "alertmanager", "req-1")
    replay = service.submit_batch([command()], "alertmanager", "req-2")
    assert replay.items[0].signal_event_id == first.items[0].signal_event_id
    assert replay.items[0].alert_id == first.items[0].alert_id
    assert replay.items[0].replayed is True
    with session_factory() as session:
        assert count(session, SignalEventRow) == 1
        assert count(session, AlertLifecycleRow) == 1
        assert count(session, IncidentEvaluationJobRow) == 1
        assert count(session, AuditEventRow) == 2


def test_same_source_identity_with_changed_content_conflicts(
    service: SignalIntakeService,
) -> None:
    service.submit_batch([command()], "alertmanager", "req-1")
    with pytest.raises(SourceEventConflict):
        service.submit_batch([command(summary="另一份规范化内容")], "alertmanager", "req-2")


def test_resolved_updates_same_alert_without_creating_second_row(
    service: SignalIntakeService, session_factory: sessionmaker[Session]
) -> None:
    firing = service.submit_batch([command()], "alertmanager", "req-1")
    resolved = service.submit_batch(
        [
            command(
                source_event_id="4" * 64,
                event_type="alert.resolved",
                event_at=NOW.replace(minute=5),
            )
        ],
        "alertmanager",
        "req-2",
    )

    assert resolved.items[0].alert_id == firing.items[0].alert_id
    assert resolved.items[0].outcome == "resolved"
    with session_factory() as session:
        assert count(session, SignalEventRow) == 2
        assert count(session, AlertLifecycleRow) == 1
        alert = session.get(AlertLifecycleRow, firing.items[0].alert_id)
        assert alert is not None
        assert alert.state == "RESOLVED"
        assert alert.resolved_at == NOW.replace(minute=5)
        jobs = tuple(
            session.scalars(
                select(IncidentEvaluationJobRow).order_by(IncidentEvaluationJobRow.alert_version)
            )
        )
        assert [(job.alert_id, job.alert_version) for job in jobs] == [
            (firing.items[0].alert_id, 1),
            (firing.items[0].alert_id, 2),
        ]


def test_same_fingerprint_with_new_episode_creates_new_alert(
    service: SignalIntakeService, session_factory: sessionmaker[Session]
) -> None:
    first = service.submit_batch([command()], "alertmanager", "req-1")
    second_started_at = NOW.replace(hour=9)
    second = service.submit_batch(
        [
            command(
                source_event_id="5" * 64,
                episode_started_at=second_started_at,
                event_at=second_started_at,
            )
        ],
        "alertmanager",
        "req-2",
    )

    assert second.items[0].alert_id != first.items[0].alert_id
    with session_factory() as session:
        assert count(session, AlertLifecycleRow) == 2


def test_late_firing_completes_orphan_resolved_without_reopening(
    service: SignalIntakeService, session_factory: sessionmaker[Session]
) -> None:
    orphan = service.submit_batch(
        [
            command(
                source_event_id="4" * 64,
                event_type="alert.resolved",
                event_at=NOW.replace(minute=5),
            )
        ],
        "alertmanager",
        "req-1",
    )
    late_firing = service.submit_batch([command()], "alertmanager", "req-2")

    assert orphan.items[0].outcome == "orphan_resolved"
    assert late_firing.items[0].alert_id == orphan.items[0].alert_id
    with session_factory() as session:
        alert = session.get(AlertLifecycleRow, orphan.items[0].alert_id)
        assert alert is not None
        assert alert.state == "RESOLVED"
        assert alert.firing_observed is True


def test_projection_failure_rolls_back_signal_event(
    service: SignalIntakeService,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_insert(self: AlertLifecycleRepository, alert: object) -> None:
        raise RuntimeError("projection_write_failed")

    monkeypatch.setattr(AlertLifecycleRepository, "insert", fail_insert)

    with pytest.raises(RuntimeError, match="projection_write_failed"):
        service.submit_batch([command()], "alertmanager", "req-1")

    with session_factory() as session:
        assert count(session, SignalEventRow) == 0
        assert count(session, SignalIntakeResultRow) == 0
        assert count(session, AlertLifecycleRow) == 0
        assert count(session, AuditEventRow) == 0


def test_evaluation_job_failure_rolls_back_signal_and_alert(
    service: SignalIntakeService,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_enqueue(
        self: IncidentEvaluationJobRepository,
        record: object,
    ) -> bool:
        raise RuntimeError("incident_evaluation_job_write_failed")

    monkeypatch.setattr(IncidentEvaluationJobRepository, "enqueue", fail_enqueue)

    with pytest.raises(RuntimeError, match="incident_evaluation_job_write_failed"):
        service.submit_batch([command()], "alertmanager", "req-1")

    with session_factory() as session:
        assert count(session, SignalEventRow) == 0
        assert count(session, SignalIntakeResultRow) == 0
        assert count(session, AlertLifecycleRow) == 0
        assert count(session, IncidentEvaluationJobRow) == 0
        assert count(session, AuditEventRow) == 0


def test_concurrent_firing_converges_to_one_alert(
    service: SignalIntakeService,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendezvous = Barrier(2)
    counter_lock = Lock()
    initial_reads = 0
    original_get = AlertLifecycleRepository.get_by_identity

    def synchronized_get(self: AlertLifecycleRepository, **kwargs: object):
        nonlocal initial_reads
        result = original_get(self, **kwargs)
        with counter_lock:
            initial_reads += 1
            should_wait = initial_reads <= 2
        if should_wait:
            rendezvous.wait(timeout=5)
        return result

    monkeypatch.setattr(AlertLifecycleRepository, "get_by_identity", synchronized_get)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(
                lambda arguments: service.submit_batch(*arguments),
                (
                    ([command(source_event_id="6" * 64)], "alertmanager", "req-1"),
                    ([command(source_event_id="7" * 64)], "alertmanager", "req-2"),
                ),
            )
        )

    assert results[0].items[0].alert_id == results[1].items[0].alert_id
    with session_factory() as session:
        assert count(session, SignalEventRow) == 2
        assert count(session, AlertLifecycleRow) == 1
