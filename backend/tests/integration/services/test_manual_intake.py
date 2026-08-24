from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.forbidden_identity import ForbiddenIdentityError
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.models import (
    AlertRow,
    AuditEventRow,
    DiagnosisRunRow,
    IncidentRow,
    IngestionKeyRow,
    SignalEventRow,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.manual_intake import (
    IdempotencyConflict,
    ManualIntakeCommand,
    ManualIntakeService,
)

NOW = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)


@pytest.fixture
def session_factory(migrated_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def command() -> ManualIntakeCommand:
    return ManualIntakeCommand(
        title="支付接口错误率升高",
        summary="支付接口在生产环境持续返回错误",
        severity="high",
        service="payment-api",
        environment="production",
        observed_at=NOW,
        facts={"region": "cn-east-1", "symptom": "high-error-rate"},
    )


@pytest.fixture
def service(session_factory: sessionmaker[Session]) -> ManualIntakeService:
    return ManualIntakeService(
        uow_factory=partial(SqlAlchemyUnitOfWork, session_factory),
        clock=lambda: NOW,
    )


def count_rows(session: Session, row_type: type[object]) -> int:
    return session.scalar(select(func.count()).select_from(row_type)) or 0


def test_manual_report_creates_four_records_and_audit_in_one_transaction(
    service: ManualIntakeService,
    command: ManualIntakeCommand,
    session_factory: sessionmaker[Session],
) -> None:
    result = service.submit(command, "key-1", "operator-a", "req-1")

    assert result.replayed is False
    with session_factory() as session:
        assert session.get(SignalEventRow, result.signal_event_id).source == "manual"  # type: ignore[union-attr]
        assert session.get(AlertRow, result.alert_id).state == "ACTIVE"  # type: ignore[union-attr]
        assert session.get(IncidentRow, result.incident_id).state == "DETECTED"  # type: ignore[union-attr]
        assert session.get(DiagnosisRunRow, result.diagnosis_run_id).state == "QUEUED"  # type: ignore[union-attr]
        assert count_rows(session, AuditEventRow) == 4
        assert count_rows(session, IngestionKeyRow) == 1


def test_same_key_and_payload_replays_existing_result(
    service: ManualIntakeService,
    command: ManualIntakeCommand,
    session_factory: sessionmaker[Session],
) -> None:
    first = service.submit(command, "key-1", "operator-a", "req-1")
    second = service.submit(command, "key-1", "operator-a", "req-2")

    assert second.model_copy(update={"replayed": False}) == first
    assert second.replayed is True
    with session_factory() as session:
        assert count_rows(session, SignalEventRow) == 1
        assert count_rows(session, AlertRow) == 1
        assert count_rows(session, IncidentRow) == 1
        assert count_rows(session, DiagnosisRunRow) == 1
        assert count_rows(session, AuditEventRow) == 4


def test_same_key_with_different_payload_conflicts(
    service: ManualIntakeService,
    command: ManualIntakeCommand,
) -> None:
    service.submit(command, "key-1", "operator-a", "req-1")
    changed = command.model_copy(update={"summary": "另一份事故描述"})

    with pytest.raises(IdempotencyConflict) as error:
        service.submit(changed, "key-1", "operator-a", "req-2")

    assert error.value.reason_code == "idempotency_conflict"


def test_concurrent_same_payload_creates_one_record_set(
    service: ManualIntakeService,
    command: ManualIntakeCommand,
    session_factory: sessionmaker[Session],
) -> None:
    def submit(request_id: str) -> object:
        return service.submit(command, "concurrent-key", "operator-a", request_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, ["req-1", "req-2"]))

    assert {result.replayed for result in results} == {False, True}  # type: ignore[attr-defined]
    assert len({result.incident_id for result in results}) == 1  # type: ignore[attr-defined]
    with session_factory() as session:
        assert count_rows(session, IncidentRow) == 1
        assert count_rows(session, AuditEventRow) == 4


def test_failure_before_diagnosis_rolls_back_every_record(
    command: ManualIntakeCommand,
    session_factory: sessionmaker[Session],
) -> None:
    def invalid_diagnosis_id(prefix: IdPrefix) -> str:
        if prefix == "diag":
            return "diag_invalid"
        return new_id(prefix)

    service = ManualIntakeService(
        uow_factory=partial(SqlAlchemyUnitOfWork, session_factory),
        clock=lambda: NOW,
        id_factory=invalid_diagnosis_id,
    )

    with pytest.raises(ValidationError):
        service.submit(command, "key-1", "operator-a", "req-1")

    with session_factory() as session:
        for row_type in (
            SignalEventRow,
            AlertRow,
            IncidentRow,
            DiagnosisRunRow,
            IngestionKeyRow,
            AuditEventRow,
        ):
            assert count_rows(session, row_type) == 0


def test_forbidden_identity_is_rejected_before_persistence(
    service: ManualIntakeService,
    command: ManualIntakeCommand,
    session_factory: sessionmaker[Session],
) -> None:
    forbidden = command.model_copy(update={"facts": {"scenario_id": "hidden"}})

    with pytest.raises(ForbiddenIdentityError):
        service.submit(forbidden, "key-1", "operator-a", "req-1")

    with session_factory() as session:
        assert count_rows(session, SignalEventRow) == 0


def test_audit_details_contain_only_identifiers_and_reason_codes(
    service: ManualIntakeService,
    command: ManualIntakeCommand,
    session_factory: sessionmaker[Session],
) -> None:
    service.submit(command, "key-1", "operator-a", "req-1")

    with session_factory() as session:
        audits = list(session.scalars(select(AuditEventRow)))

    serialized_details = " ".join(str(audit.details) for audit in audits)
    assert command.summary not in serialized_details
    assert "key-1" not in serialized_details
    assert all(set(audit.details) <= {"reason_code", "parent_id"} for audit in audits)
