from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from incident_intelligence.persistence.models import (
    AlertLifecycleRow,
    AlertSourceRow,
    OperationalIncidentAlertRow,
)
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
    _link_alert(migrated_engine)
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
    assert snapshot.alert_facts[0].alert_name == "HighErrorRate"


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


def _link_alert(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(
            AlertSourceRow(
                id="src_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                name="诊断测试来源",
                source_type="ALERTMANAGER",
                management_type="USER_MANAGED",
                state="ENABLED",
                environment="testing",
                environment_name="测试环境",
                environment_configured=True,
                version=1,
                last_accepted_at=None,
                last_rejected_at=None,
                last_validated_at=None,
                accepted_requests=0,
                rejected_requests=0,
                opened_count=0,
                updated_count=0,
                resolved_count=0,
                replayed_count=0,
                ignored_count=0,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            AlertLifecycleRow(
                id="alt_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                alert_source_id="src_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                source_alert_key="HighErrorRate/checkout",
                episode_started_at=NOW,
                alert_name="HighErrorRate",
                summary="错误率升高",
                description="checkout 服务错误率升高",
                state="ACTIVE",
                severity="high",
                environment="testing",
                service="checkout",
                entity_type="SERVICE",
                entity_key="c" * 64,
                entity_display_name="checkout",
                first_observed_at=NOW,
                last_observed_at=NOW,
                first_received_at=NOW,
                last_received_at=NOW,
                resolved_at=None,
                firing_observed=True,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            OperationalIncidentAlertRow(
                incident_id=INCIDENT_ID,
                alert_id="alt_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                incident_rule_version=1,
                first_trigger_window=True,
                linked_at=NOW,
            )
        )
        session.commit()
