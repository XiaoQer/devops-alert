from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.engine import Engine

from incident_intelligence.persistence.session import make_session_factory
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.incident_diagnosis import (
    DiagnosisRunAlreadyActive,
    IncidentDiagnosisService,
)
from tests.integration.services.test_diagnosis_repository import (
    EVIDENCE_RUN_ID,
    INCIDENT_ID,
    _seed_parent_rows,
)

NOW = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)


def test_manual_diagnosis_freezes_selected_evidence_run(
    migrated_engine: Engine,
) -> None:
    _seed_parent_rows(migrated_engine)
    service = _service(migrated_engine)

    result = service.request_manual(
        INCIDENT_ID,
        evidence_run_id=EVIDENCE_RUN_ID,
        actor="operator",
        idempotency_key="first-request",
        request_id="req-1",
    )

    assert result.replayed is False
    assert result.run.state == "QUEUED"
    with SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)) as uow:
        assert uow.diagnosis_runs is not None
        snapshot = uow.diagnosis_runs.get_snapshot(result.run.id)
    assert snapshot is not None
    assert snapshot.evidence_run_id == EVIDENCE_RUN_ID
    assert snapshot.environment == "testing"
    assert snapshot.service_name == "checkout"


def test_second_active_diagnosis_for_one_incident_is_rejected(
    migrated_engine: Engine,
) -> None:
    _seed_parent_rows(migrated_engine)
    service = _service(migrated_engine)
    service.request_manual(
        INCIDENT_ID,
        evidence_run_id=EVIDENCE_RUN_ID,
        actor="operator",
        idempotency_key="first-request",
        request_id="req-1",
    )

    with pytest.raises(DiagnosisRunAlreadyActive):
        service.request_manual(
            INCIDENT_ID,
            evidence_run_id=EVIDENCE_RUN_ID,
            actor="operator",
            idempotency_key="second-request",
            request_id="req-2",
        )


def _service(engine: Engine) -> IncidentDiagnosisService:
    sequence = iter(range(1, 100))
    return IncidentDiagnosisService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(engine)),
        clock=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}_{next(sequence):032x}",
    )
