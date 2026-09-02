from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import MetaData, Table, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from incident_intelligence.adapters.monitoring_http import AdapterEvidenceResult
from incident_intelligence.domain.evidence import (
    EvidenceContext,
    EvidenceRun,
    build_evidence_window,
)
from incident_intelligence.persistence.evidence_repository import (
    EvidenceRunRepository,
    EvidenceTaskRecord,
    EvidenceTaskRepository,
    MonitoringDataSourceRecord,
    MonitoringDataSourceRepository,
)
from incident_intelligence.persistence.models import (
    EvidenceCollectionTaskRow,
    EvidenceItemRow,
    EvidenceRunRow,
    OperationalIncidentActivityRow,
)
from incident_intelligence.persistence.session import make_session_factory
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.evidence_collection import EvidenceCollectionService
from incident_intelligence.services.incident_evidence import IncidentEvidenceService

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
INCIDENT_ID = "inc_11111111111111111111111111111111"
RUN_ID = "evr_22222222222222222222222222222222"
TASK_ID = "evtask_33333333333333333333333333333333"


class _SuccessfulAdapter:
    def collect(self, request):
        return AdapterEvidenceResult(
            state="SUCCEEDED",
            normalized_result={"query": request.query_name},
            baseline_summary={"sample_count": 2},
            fault_summary={"sample_count": 3},
            interpretation="已取得指标数据。",
        )


class _FakeAdapterFactory:
    def create(self, source):
        assert source.source_type == "PROMETHEUS"
        return _SuccessfulAdapter()


def test_collection_preserves_prometheus_results_when_other_sources_are_missing(
    migrated_engine: Engine,
) -> None:
    _seed(migrated_engine)
    sequence = iter(range(1, 100))
    service = EvidenceCollectionService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
        adapter_factory=_FakeAdapterFactory(),
        clock=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}_{next(sequence):032x}",
        owner="worker-1",
    )

    result = service.process(TASK_ID)

    assert result.outcome == "PARTIAL"
    with Session(migrated_engine) as session:
        run = session.get(EvidenceRunRow, RUN_ID)
        assert run is not None
        assert run.state == "PARTIAL"
        assert run.succeeded_count >= 1
        assert run.skipped_count >= 1
        task = session.get(EvidenceCollectionTaskRow, TASK_ID)
        assert task is not None and task.state == "SUCCEEDED"
        assert session.scalar(select(func.count()).select_from(EvidenceItemRow)) >= 4
        activity = session.scalar(
            select(OperationalIncidentActivityRow).where(
                OperationalIncidentActivityRow.kind == "EVIDENCE_COLLECTION_PARTIAL"
            )
        )
        assert activity is not None
        assert activity.incident_id == INCIDENT_ID


def test_manual_collection_request_is_idempotent_and_keeps_history(
    migrated_engine: Engine,
) -> None:
    _seed(migrated_engine)
    sequence = iter(range(100, 200))
    collection = EvidenceCollectionService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
        adapter_factory=_FakeAdapterFactory(),
        clock=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}_{next(sequence):032x}",
        owner="worker-1",
    )
    collection.process(TASK_ID)
    service = IncidentEvidenceService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
        clock=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}_{next(sequence):032x}",
    )

    first = service.request_manual(
        INCIDENT_ID,
        idempotency_key="manual-1",
        actor="tester",
        request_id="req-1",
    )
    replay = service.request_manual(
        INCIDENT_ID,
        idempotency_key="manual-1",
        actor="tester",
        request_id="req-2",
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.run.id == first.run.id
    assert service.list_runs(INCIDENT_ID).total == 2
    detail = service.get_run(INCIDENT_ID, first.run.id)
    assert detail.run.trigger == "MANUAL"
    assert detail.items == ()


def _seed(engine: Engine) -> None:
    with Session(engine) as session:
        rules = Table("incident_rules", MetaData(), autoload_with=session.bind)
        incidents = Table("operational_incidents", MetaData(), autoload_with=session.bind)
        session.execute(
            rules.insert().values(
                id="irl_66666666666666666666666666666666",
                name="测试规则",
                description="测试",
                state="PUBLISHED",
                environment="testing",
                alert_source_ids=[],
                services=["checkout"],
                group_by="SERVICE",
                window_minutes=5,
                conditions=[{"type": "ACTIVE_ALERTS_GTE", "threshold": 1}],
                summary="测试",
                version=1,
                last_successful_dry_run_id="ird_77777777777777777777777777777777",
                last_successful_dry_run_version=1,
                last_successful_dry_run_at=NOW,
                published_at=NOW,
                disabled_at=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.execute(
            incidents.insert().values(
                id=INCIDENT_ID,
                reference="INC-20260902-001",
                title="testing checkout异常",
                state="OPEN",
                severity="high",
                environment="testing",
                group_by="SERVICE",
                group_key="checkout",
                group_display_name="checkout",
                incident_rule_id="irl_66666666666666666666666666666666",
                incident_rule_version=1,
                open_boundary_key="a" * 64,
                alert_count=1,
                active_alert_count=1,
                distinct_alert_name_count=1,
                version=1,
                opened_at=NOW,
                acknowledged_at=None,
                resolved_at=None,
                resolution_summary=None,
                last_alert_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        run = EvidenceRun(
            id=RUN_ID,
            incident_id=INCIDENT_ID,
            trigger="AUTOMATIC",
            state="QUEUED",
            anchor_at=NOW,
            window=build_evidence_window(NOW, NOW),
            context=EvidenceContext(
                environment="testing",
                service_name="checkout",
                alert_names=("HighErrorRate",),
            ),
            requested_by="incident-evaluation",
            created_at=NOW,
        )
        EvidenceRunRepository(session).insert(run)
        EvidenceTaskRepository(session).insert(
            EvidenceTaskRecord(
                id=TASK_ID,
                evidence_run_id=RUN_ID,
                state="PENDING",
                attempt_count=0,
                next_attempt_at=NOW,
                lease_owner=None,
                lease_until=None,
                last_error_code=None,
                completed_at=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        MonitoringDataSourceRepository(session).insert(
            MonitoringDataSourceRecord(
                id="mds_88888888888888888888888888888888",
                name="Prometheus",
                environment="testing",
                source_type="PROMETHEUS",
                base_url="http://prometheus:9090",
                credential_env_key=None,
                field_mapping={},
                verify_tls=True,
                enabled=True,
                version=1,
                last_test_state=None,
                last_test_latency_ms=None,
                last_compatible_version=None,
                last_test_error_code=None,
                last_tested_at=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.commit()
