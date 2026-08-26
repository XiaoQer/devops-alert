from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.signal_intake import SignalCommand
from incident_intelligence.persistence.models import (
    AlertRow,
    AlertSourceReceiptRow,
    AlertSourceRow,
    CorrelationJobRow,
    IncidentRow,
    SignalEventRow,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_sources import (
    AlertSourceService,
    CreateAlertSourceCommand,
)
from incident_intelligence.services.signal_intake import SignalIntakeService, SourceEventConflict
from incident_intelligence.services.source_receipts import (
    ReceiptContext,
    SourceReceiptService,
)

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


@pytest.fixture
def session_factory(migrated_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


def _create_source(session_factory: sessionmaker[Session]):
    service = AlertSourceService(
        uow_factory=partial(SqlAlchemyUnitOfWork, session_factory),
        clock=lambda: NOW,
    )
    return service.create_source(
        CreateAlertSourceCommand(name="动态 Alertmanager", source_type="ALERTMANAGER"),
        idempotency_key="create-source",
        actor="manual-api-client",
        request_id="req-create",
    )


def _command(source_id: str, *, event_id: str = "2" * 64) -> SignalCommand:
    return SignalCommand(
        alert_source_id=source_id,
        source="alertmanager",
        source_instance="1" * 64,
        source_event_id=event_id,
        source_alert_key="payment-errors",
        event_type="alert.firing",
        event_at=NOW,
        episode_started_at=NOW,
        title="支付接口错误率升高",
        summary="支付接口错误率超过阈值",
        severity="high",
        service="payment-api",
        environment="production",
        facts={},
    )


def _count(session: Session, row_type: type[object]) -> int:
    return session.scalar(select(func.count()).select_from(row_type)) or 0


def test_success_and_replay_write_safe_receipts_in_domain_transaction(
    session_factory: sessionmaker[Session],
) -> None:
    created = _create_source(session_factory)
    source_id = created.source.id
    intake = SignalIntakeService(
        uow_factory=partial(SqlAlchemyUnitOfWork, session_factory),
        clock=lambda: NOW,
    )
    first = intake.submit_batch(
        (_command(source_id),),
        actor="alert-source",
        request_id="req-intake-1",
        receipt_context=ReceiptContext(
            alert_source_id=source_id,
            adapter_type="ALERTMANAGER",
            request_id="req-intake-1",
            received_at=NOW,
        ),
    )
    replay = intake.submit_batch(
        (_command(source_id),),
        actor="alert-source",
        request_id="req-intake-2",
        receipt_context=ReceiptContext(
            alert_source_id=source_id,
            adapter_type="ALERTMANAGER",
            request_id="req-intake-2",
            received_at=NOW + timedelta(seconds=1),
        ),
    )

    assert first.counts.opened == 1
    assert replay.counts.replayed == 1
    with session_factory() as session:
        receipts = list(
            session.scalars(
                select(AlertSourceReceiptRow).order_by(AlertSourceReceiptRow.received_at)
            )
        )
        source = session.get(AlertSourceRow, source_id)
        assert _count(session, SignalEventRow) == 1
        assert _count(session, AlertRow) == 1
        assert _count(session, CorrelationJobRow) == 1
        assert _count(session, IncidentRow) == 0
    assert [row.outcome for row in receipts] == ["ACCEPTED", "REPLAYED"]
    assert [row.request_id for row in receipts] == ["req-intake-1", "req-intake-2"]
    assert source is not None
    assert source.accepted_requests == 2
    assert source.opened_count == 1
    assert source.replayed_count == 1


def test_authenticated_failure_and_validation_write_only_bounded_safe_receipts(
    session_factory: sessionmaker[Session],
) -> None:
    created = _create_source(session_factory)
    source_id = created.source.id
    service = SourceReceiptService(
        uow_factory=partial(SqlAlchemyUnitOfWork, session_factory),
        clock=lambda: NOW,
    )
    service.record(
        ReceiptContext(
            alert_source_id=source_id,
            adapter_type="ALERTMANAGER",
            request_id="req-invalid",
            received_at=NOW,
        ),
        outcome="PAYLOAD_REJECTED",
        reason_code="invalid_source_payload",
        input_count=0,
    )
    service.record(
        ReceiptContext(
            alert_source_id=source_id,
            adapter_type="ALERTMANAGER",
            request_id="req-validate",
            received_at=NOW + timedelta(seconds=1),
        ),
        outcome="VALIDATED",
        reason_code="source_payload_validated",
        input_count=1,
    )

    with session_factory() as session:
        receipts = list(session.scalars(select(AlertSourceReceiptRow)))
        source = session.get(AlertSourceRow, source_id)
        assert _count(session, SignalEventRow) == 0
        assert _count(session, AlertRow) == 0
        assert _count(session, CorrelationJobRow) == 0
    assert len(receipts) == 2
    assert all(
        set(vars(row))
        >= {
            "alert_source_id",
            "adapter_type",
            "outcome",
            "reason_code",
            "request_id",
        }
        for row in receipts
    )
    assert source is not None
    assert source.rejected_requests == 1
    assert source.last_rejected_at == NOW
    assert source.last_validated_at == NOW + timedelta(seconds=1)


def test_receipt_retention_removes_old_rows(
    session_factory: sessionmaker[Session],
) -> None:
    created = _create_source(session_factory)
    source_id = created.source.id
    with session_factory.begin() as session:
        session.add(
            AlertSourceReceiptRow(
                id="rcp_" + "f" * 32,
                alert_source_id=source_id,
                adapter_type="ALERTMANAGER",
                outcome="VALIDATED",
                reason_code="old",
                request_id="req-old",
                input_count=1,
                opened_count=0,
                updated_count=0,
                resolved_count=0,
                replayed_count=0,
                ignored_count=0,
                received_at=NOW - timedelta(days=31),
            )
        )
    SourceReceiptService(
        uow_factory=partial(SqlAlchemyUnitOfWork, session_factory),
        clock=lambda: NOW,
    ).record(
        ReceiptContext(
            alert_source_id=source_id,
            adapter_type="ALERTMANAGER",
            request_id="req-new",
            received_at=NOW,
        ),
        outcome="VALIDATED",
        reason_code="source_payload_validated",
        input_count=1,
    )
    with session_factory() as session:
        assert _count(session, AlertSourceReceiptRow) == 1


def test_receipt_retention_keeps_at_most_one_thousand_rows(
    session_factory: sessionmaker[Session],
) -> None:
    created = _create_source(session_factory)
    source_id = created.source.id
    with session_factory.begin() as session:
        session.add_all(
            [
                AlertSourceReceiptRow(
                    id=f"rcp_{index:032x}",
                    alert_source_id=source_id,
                    adapter_type="ALERTMANAGER",
                    outcome="VALIDATED",
                    reason_code="existing",
                    request_id=f"req-{index}",
                    input_count=1,
                    opened_count=0,
                    updated_count=0,
                    resolved_count=0,
                    replayed_count=0,
                    ignored_count=0,
                    received_at=NOW - timedelta(seconds=1_000 - index),
                )
                for index in range(1_000)
            ]
        )
    SourceReceiptService(
        uow_factory=partial(SqlAlchemyUnitOfWork, session_factory),
        clock=lambda: NOW,
    ).record(
        ReceiptContext(
            alert_source_id=source_id,
            adapter_type="ALERTMANAGER",
            request_id="req-newest",
            received_at=NOW,
        ),
        outcome="VALIDATED",
        reason_code="source_payload_validated",
        input_count=1,
    )
    with session_factory() as session:
        assert _count(session, AlertSourceReceiptRow) == 1_000
        assert session.get(AlertSourceReceiptRow, "rcp_" + "0" * 32) is None


def test_failed_batch_rolls_back_new_domain_rows_and_success_receipt(
    session_factory: sessionmaker[Session],
) -> None:
    created = _create_source(session_factory)
    source_id = created.source.id
    intake = SignalIntakeService(
        uow_factory=partial(SqlAlchemyUnitOfWork, session_factory),
        clock=lambda: NOW,
    )
    context = ReceiptContext(
        alert_source_id=source_id,
        adapter_type="ALERTMANAGER",
        request_id="req-existing",
        received_at=NOW,
    )
    intake.submit_batch(
        (_command(source_id),),
        actor="alert-source",
        request_id=context.request_id,
        receipt_context=context,
    )

    with pytest.raises(SourceEventConflict):
        intake.submit_batch(
            (
                _command(source_id, event_id="3" * 64),
                _command(source_id, event_id="2" * 64).model_copy(
                    update={"summary": "同一事件身份的冲突内容"}
                ),
            ),
            actor="alert-source",
            request_id="req-failed",
            receipt_context=ReceiptContext(
                alert_source_id=source_id,
                adapter_type="ALERTMANAGER",
                request_id="req-failed",
                received_at=NOW + timedelta(seconds=1),
            ),
        )
    with session_factory() as session:
        assert _count(session, SignalEventRow) == 1
        assert _count(session, AlertRow) == 1
        assert _count(session, CorrelationJobRow) == 1
        assert _count(session, AlertSourceReceiptRow) == 1
