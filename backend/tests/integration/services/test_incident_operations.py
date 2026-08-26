from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.alert_sources import MANUAL_SYSTEM_SOURCE_ID
from incident_intelligence.domain.enums import IncidentState
from incident_intelligence.domain.incident_operations import (
    IncidentNoteCategory,
    IncidentResolutionCategory,
)
from incident_intelligence.ids import new_id
from incident_intelligence.persistence.models import (
    AlertRow,
    AuditEventRow,
    IncidentActivityRow,
    IncidentOperationRow,
    IncidentRow,
    SignalEventRow,
)
from incident_intelligence.persistence.repositories import RecordRepositories
from incident_intelligence.services.incident_center import IncidentResourceNotFound
from incident_intelligence.services.incident_operations import (
    IncidentAlreadyClaimed,
    IncidentClosed,
    IncidentNotClaimed,
    IncidentOperationConflict,
    IncidentOperationService,
    IncidentVersionConflict,
)

NOW = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)


@pytest.fixture
def session_factory(migrated_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def service(session_factory: sessionmaker[Session]) -> IncidentOperationService:
    return IncidentOperationService(session_factory=session_factory, clock=lambda: NOW)


def seed_incident(
    session_factory: sessionmaker[Session],
    *,
    state: str = "DETECTED",
    assignee: str | None = None,
    version: int = 1,
) -> str:
    signal_id = new_id("sig")
    alert_id = new_id("alt")
    incident_id = new_id("inc")
    resolved_at = NOW if state in {"RESOLVED", "CLOSED"} else None
    with session_factory.begin() as session:
        session.add(
            SignalEventRow(
                id=signal_id,
                alert_source_id=MANUAL_SYSTEM_SOURCE_ID,
                source="manual",
                source_event_id=new_id("sig"),
                event_type="manual.reported",
                title="支付接口错误率升高",
                summary="支付接口持续返回错误",
                severity="high",
                service="payment-api",
                environment="production",
                observed_at=NOW,
                received_at=NOW,
                facts={"region": "cn-east-1"},
                payload_fingerprint="a" * 64,
                created_at=NOW,
                version=1,
            )
        )
        session.flush()
        session.add(
            AlertRow(
                id=alert_id,
                signal_event_id=signal_id,
                alert_source_id=MANUAL_SYSTEM_SOURCE_ID,
                source="manual",
                source_instance="b" * 64,
                source_alert_key=new_id("alt"),
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
        session.flush()
        session.add(
            IncidentRow(
                id=incident_id,
                primary_alert_id=alert_id,
                state=state,
                title="支付接口错误率升高",
                severity="high",
                service="payment-api",
                environment="production",
                detected_at=NOW,
                assignee=assignee,
                claimed_at=NOW if assignee is not None else None,
                state_changed_at=NOW,
                resolved_at=resolved_at,
                closed_at=NOW if state == "CLOSED" else None,
                created_at=NOW,
                version=version,
            )
        )
    return incident_id


def count_rows(
    session_factory: sessionmaker[Session], row_type: type[object], incident_id: str
) -> int:
    with session_factory() as session:
        return (
            session.scalar(
                select(func.count())
                .select_from(row_type)
                .where(
                    row_type.incident_id == incident_id  # type: ignore[attr-defined]
                )
            )
            or 0
        )


def test_claim_transition_release_are_atomic_and_audited(
    service: IncidentOperationService,
    session_factory: sessionmaker[Session],
) -> None:
    incident_id = seed_incident(session_factory)

    claimed = service.claim(
        incident_id,
        expected_version=1,
        actor="manual-api-client",
        idempotency_key="claim-1",
        request_id="req-claim",
    )
    transitioned = service.transition(
        incident_id,
        expected_version=2,
        target_state=IncidentState.INVESTIGATING,
        message="开始检查错误实例",
        actor="manual-api-client",
        idempotency_key="transition-1",
        request_id="req-transition",
    )
    released = service.release(
        incident_id,
        expected_version=3,
        actor="manual-api-client",
        idempotency_key="release-1",
        request_id="req-release",
    )

    assert (claimed.version, transitioned.version, released.version) == (2, 3, 4)
    with session_factory() as session:
        activities = tuple(
            session.scalars(
                select(IncidentActivityRow)
                .where(IncidentActivityRow.incident_id == incident_id)
                .order_by(IncidentActivityRow.incident_version)
            )
        )
        assert tuple(activity.kind for activity in activities) == (
            "INCIDENT_CLAIMED",
            "STATE_TRANSITIONED",
            "INCIDENT_RELEASED",
        )
        assert all(
            set(audit.details) <= {"reason_code", "activity_id"}
            for audit in session.scalars(select(AuditEventRow))
        )


def test_resolution_reopen_and_close_cycle_preserves_history(
    service: IncidentOperationService,
    session_factory: sessionmaker[Session],
) -> None:
    incident_id = seed_incident(session_factory)
    note = service.add_note(
        incident_id,
        expected_version=1,
        category=IncidentNoteCategory.CURRENT_FINDING,
        message="错误集中在两个实例",
        actor="manual-api-client",
        idempotency_key="note-1",
        request_id="req-note",
    )
    resolved = service.resolve(
        incident_id,
        expected_version=note.version,
        category=IncidentResolutionCategory.RECOVERED,
        message="错误率已经恢复",
        resolution_actions="隔离异常实例并扩容",
        root_cause=None,
        actor="manual-api-client",
        idempotency_key="resolve-1",
        request_id="req-resolve",
    )
    reopened = service.reopen(
        incident_id,
        expected_version=resolved.version,
        reason="错误率再次升高",
        actor="manual-api-client",
        idempotency_key="reopen-1",
        request_id="req-reopen",
    )
    resolved_again = service.resolve(
        incident_id,
        expected_version=reopened.version,
        category=IncidentResolutionCategory.FALSE_POSITIVE,
        message="确认是采集抖动",
        resolution_actions="修正采集配置",
        root_cause="采集窗口配置错误",
        actor="manual-api-client",
        idempotency_key="resolve-2",
        request_id="req-resolve-2",
    )
    closed = service.close(
        incident_id,
        expected_version=resolved_again.version,
        message="复盘完成并关闭事故",
        actor="manual-api-client",
        idempotency_key="close-1",
        request_id="req-close",
    )

    assert (resolved.state, reopened.state, closed.state) == (
        IncidentState.RESOLVED,
        IncidentState.INVESTIGATING,
        IncidentState.CLOSED,
    )
    with session_factory() as session:
        incident = session.get(IncidentRow, incident_id)
        assert incident is not None
        assert incident.resolved_at == NOW
        assert incident.closed_at == NOW
        kinds = tuple(
            session.scalars(
                select(IncidentActivityRow.kind)
                .where(IncidentActivityRow.incident_id == incident_id)
                .order_by(IncidentActivityRow.incident_version)
            )
        )
        assert kinds.count("INCIDENT_RESOLVED") == 2
        assert kinds == (
            "NOTE_ADDED",
            "INCIDENT_RESOLVED",
            "INCIDENT_REOPENED",
            "INCIDENT_RESOLVED",
            "INCIDENT_CLOSED",
        )


def test_exact_replay_and_same_key_conflict_are_safe(
    service: IncidentOperationService,
    session_factory: sessionmaker[Session],
) -> None:
    incident_id = seed_incident(session_factory)
    command = {
        "incident_id": incident_id,
        "expected_version": 1,
        "category": IncidentNoteCategory.CURRENT_FINDING,
        "message": "错误集中在两个实例",
        "actor": "manual-api-client",
        "idempotency_key": "same-key",
        "request_id": "req-note",
    }

    first = service.add_note(**command)
    replay = service.add_note(**command)
    assert replay == first
    with pytest.raises(IncidentOperationConflict):
        service.add_note(**{**command, "message": "另一条内容", "request_id": "req-2"})
    assert count_rows(session_factory, IncidentActivityRow, incident_id) == 1
    assert count_rows(session_factory, IncidentOperationRow, incident_id) == 1
    with session_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(AuditEventRow.resource_id == incident_id)
            )
            == 1
        )


@pytest.mark.parametrize(
    ("action", "initial_state", "assignee", "version", "result_state"),
    [
        ("claim", "DETECTED", None, 1, "DETECTED"),
        ("release", "DETECTED", "manual-api-client", 2, "DETECTED"),
        ("transition", "DETECTED", None, 1, "TRIAGING"),
        ("note", "DETECTED", None, 1, "DETECTED"),
        ("resolve", "DETECTED", None, 1, "RESOLVED"),
        ("reopen", "RESOLVED", None, 2, "INVESTIGATING"),
        ("close", "RESOLVED", None, 2, "CLOSED"),
    ],
)
def test_every_operation_replays_exactly_once(
    service: IncidentOperationService,
    session_factory: sessionmaker[Session],
    action: str,
    initial_state: str,
    assignee: str | None,
    version: int,
    result_state: str,
) -> None:
    incident_id = seed_incident(
        session_factory,
        state=initial_state,
        assignee=assignee,
        version=version,
    )
    key = f"replay-{action}"

    def invoke(target_id: str) -> object:
        common = {
            "incident_id": target_id,
            "expected_version": version,
            "actor": "manual-api-client",
            "idempotency_key": key,
            "request_id": f"req-{action}",
        }
        if action == "claim":
            return service.claim(**common)
        if action == "release":
            return service.release(**common)
        if action == "transition":
            return service.transition(
                **common,
                target_state=IncidentState.TRIAGING,
                message="开始分诊",
            )
        if action == "note":
            return service.add_note(
                **common,
                category=IncidentNoteCategory.GENERAL,
                message="补充排查记录",
            )
        if action == "resolve":
            return service.resolve(
                **common,
                category=IncidentResolutionCategory.RECOVERED,
                message="服务恢复",
                resolution_actions="隔离异常实例",
                root_cause=None,
            )
        if action == "reopen":
            return service.reopen(**common, reason="异常再次发生")
        return service.close(**common, message="复盘完成")

    first = invoke(incident_id)
    assert invoke(incident_id) == first
    assert first.state == result_state  # type: ignore[attr-defined]
    assert count_rows(session_factory, IncidentActivityRow, incident_id) == 1
    assert count_rows(session_factory, IncidentOperationRow, incident_id) == 1

    other_id = seed_incident(
        session_factory,
        state=initial_state,
        assignee=assignee,
        version=version,
    )
    with pytest.raises(IncidentOperationConflict):
        invoke(other_id)
    assert count_rows(session_factory, IncidentActivityRow, other_id) == 0


def test_stale_missing_claim_and_closed_fail_without_partial_rows(
    service: IncidentOperationService,
    session_factory: sessionmaker[Session],
) -> None:
    incident_id = seed_incident(session_factory)
    with pytest.raises(IncidentVersionConflict):
        service.transition(
            incident_id,
            expected_version=99,
            target_state=IncidentState.INVESTIGATING,
            message="开始调查",
            actor="manual-api-client",
            idempotency_key="stale",
            request_id="req-stale",
        )
    with pytest.raises(IncidentNotClaimed):
        service.release(
            incident_id,
            expected_version=1,
            actor="manual-api-client",
            idempotency_key="release",
            request_id="req-release",
        )
    closed_id = seed_incident(session_factory, state="CLOSED", version=4)
    with pytest.raises(IncidentClosed):
        service.add_note(
            closed_id,
            expected_version=4,
            category=IncidentNoteCategory.GENERAL,
            message="不应写入",
            actor="manual-api-client",
            idempotency_key="closed-note",
            request_id="req-closed",
        )
    with pytest.raises(IncidentResourceNotFound):
        service.claim(
            "inc_" + "f" * 32,
            expected_version=1,
            actor="manual-api-client",
            idempotency_key="missing",
            request_id="req-missing",
        )
    assert count_rows(session_factory, IncidentActivityRow, incident_id) == 0
    assert count_rows(session_factory, IncidentOperationRow, incident_id) == 0


def test_claim_conflict_and_transaction_failure_roll_back(
    service: IncidentOperationService,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claimed_id = seed_incident(session_factory, assignee="another-operator", version=2)
    with pytest.raises(IncidentAlreadyClaimed):
        service.claim(
            claimed_id,
            expected_version=2,
            actor="manual-api-client",
            idempotency_key="claim-conflict",
            request_id="req-conflict",
        )

    incident_id = seed_incident(session_factory)

    def fail_audit(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("injected_audit_failure")

    monkeypatch.setattr(RecordRepositories, "add_audit", fail_audit)
    with pytest.raises(RuntimeError, match="injected_audit_failure"):
        service.add_note(
            incident_id,
            expected_version=1,
            category=IncidentNoteCategory.GENERAL,
            message="事务必须回滚",
            actor="manual-api-client",
            idempotency_key="rollback",
            request_id="req-rollback",
        )
    with session_factory() as session:
        incident = session.get(IncidentRow, incident_id)
        assert incident is not None
        assert incident.version == 1
    assert count_rows(session_factory, IncidentActivityRow, incident_id) == 0
    assert count_rows(session_factory, IncidentOperationRow, incident_id) == 0


def test_concurrent_writes_converge_to_one_version(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id = seed_incident(session_factory)
    barrier = Barrier(2)

    def submit(index: int) -> object:
        local = IncidentOperationService(
            session_factory=session_factory,
            clock=lambda: NOW + timedelta(seconds=index),
            before_lock=barrier.wait,
        )
        try:
            return local.add_note(
                incident_id,
                expected_version=1,
                category=IncidentNoteCategory.GENERAL,
                message=f"并发记录 {index}",
                actor="manual-api-client",
                idempotency_key=f"concurrent-{index}",
                request_id=f"req-{index}",
            )
        except IncidentVersionConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(submit, (1, 2)))

    assert sum(not isinstance(item, IncidentVersionConflict) for item in outcomes) == 1
    assert count_rows(session_factory, IncidentActivityRow, incident_id) == 1
    assert count_rows(session_factory, IncidentOperationRow, incident_id) == 1
