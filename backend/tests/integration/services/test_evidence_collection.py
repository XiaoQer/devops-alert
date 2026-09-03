# ruff: noqa: RUF001

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import MetaData, Table, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from incident_intelligence.adapters.monitoring_http import (
    AdapterEvidenceResult,
    MonitoringPermanentError,
    MonitoringRetryableError,
)
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
from incident_intelligence.services.incident_evidence import (
    EvidenceRunAlreadyActive,
    EvidenceRunNotFound,
    IncidentEvidenceService,
)

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
INCIDENT_ID = "inc_11111111111111111111111111111111"
RUN_ID = "evr_22222222222222222222222222222222"
TASK_ID = "evtask_33333333333333333333333333333333"


class _SuccessfulAdapter:
    def collect(self, request):
        return AdapterEvidenceResult(
            state="SUCCEEDED",
            normalized_result={"query": request.query_name},
            baseline_summary={"sample_count": 2, "average": 1.0},
            fault_summary={"sample_count": 3, "average": 0.8},
            interpretation="已取得指标数据。",
        )


class _FakeAdapterFactory:
    def create(self, source):
        assert source.source_type in {"PROMETHEUS", "ELASTICSEARCH", "SKYWALKING"}
        return _SuccessfulAdapter()


class _RetryingAdapter:
    def collect(self, request):
        del request
        raise MonitoringRetryableError("network_timeout")


class _RetryingAdapterFactory:
    def create(self, source):
        del source
        return _RetryingAdapter()


class _UnexpectedAdapterFactory:
    def create(self, source):
        raise AssertionError(f"不应创建适配器: {source.source_type}")


class _PermanentFailureFactory:
    def create(self, source):
        del source
        raise MonitoringPermanentError("http_authentication_failed")


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
        availability = session.scalar(
            select(EvidenceItemRow).where(
                EvidenceItemRow.template_id == "prom.service.availability"
            )
        )
        assert availability is not None
        assert availability.interpretation == (
            "故障前服务可用性平均值为 1，故障期间为 0.8，下降了 0.2。"
        )
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


def test_retryable_monitoring_failure_reschedules_task(migrated_engine: Engine) -> None:
    _seed(migrated_engine)
    service = EvidenceCollectionService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
        adapter_factory=_RetryingAdapterFactory(),
        clock=lambda: NOW,
        owner="worker-1",
    )

    result = service.process(TASK_ID)

    assert result.outcome == "RETRY_SCHEDULED"
    with Session(migrated_engine) as session:
        task = session.get(EvidenceCollectionTaskRow, TASK_ID)
        assert task is not None
        assert task.state == "PENDING"
        assert task.attempt_count == 1
        assert task.last_error_code == "network_timeout"


def test_missing_sources_finish_as_failed_and_replay_without_duplicate_items(
    migrated_engine: Engine,
) -> None:
    _seed(migrated_engine, add_source=False)
    service = EvidenceCollectionService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
        adapter_factory=_UnexpectedAdapterFactory(),
        clock=lambda: NOW,
        owner="worker-1",
    )

    first = service.process(TASK_ID)
    replay = service.process(TASK_ID)

    assert first.outcome == "FAILED"
    assert first.skipped_count >= 4
    assert replay.replayed is True


def test_missing_service_target_is_recorded_without_calling_sources(
    migrated_engine: Engine,
) -> None:
    _seed(migrated_engine, service_name=None)
    service = EvidenceCollectionService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
        adapter_factory=_UnexpectedAdapterFactory(),
        clock=lambda: NOW,
        owner="worker-1",
    )

    result = service.process(TASK_ID)

    assert result.outcome == "FAILED"
    assert result.missing_count >= 4


def test_manual_request_rejects_active_run_and_cross_incident_lookup(
    migrated_engine: Engine,
) -> None:
    _seed(migrated_engine)
    service = IncidentEvidenceService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
        clock=lambda: NOW,
    )

    with pytest.raises(EvidenceRunAlreadyActive) as active:
        service.request_manual(
            INCIDENT_ID,
            idempotency_key="manual-active",
            actor="tester",
            request_id="req-1",
        )
    assert active.value.active_run_id == RUN_ID
    with pytest.raises(EvidenceRunNotFound):
        service.get_run("inc_99999999999999999999999999999999", RUN_ID)


def test_all_configured_sources_can_finish_a_successful_run(migrated_engine: Engine) -> None:
    _seed(migrated_engine)
    with Session(migrated_engine) as session:
        sources = MonitoringDataSourceRepository(session)
        sources.insert(
            _source(
                "mds_99999999999999999999999999999991",
                "ELASTICSEARCH",
                {"index": "logs-*"},
            )
        )
        sources.insert(
            _source(
                "mds_99999999999999999999999999999992",
                "SKYWALKING",
                {"graphql_path": "/graphql"},
            )
        )
        session.commit()
    sequence = iter(range(200, 300))
    service = EvidenceCollectionService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
        adapter_factory=_FakeAdapterFactory(),
        clock=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}_{next(sequence):032x}",
        owner="worker-1",
    )

    result = service.process(TASK_ID)

    assert result.outcome == "SUCCEEDED"
    assert result.failed_count == 0
    assert result.skipped_count == 0


def test_last_retry_becomes_failed_evidence_instead_of_an_endless_task(
    migrated_engine: Engine,
) -> None:
    _seed(migrated_engine)
    with Session(migrated_engine) as session:
        task = session.get(EvidenceCollectionTaskRow, TASK_ID)
        assert task is not None
        task.attempt_count = 4
        session.commit()
    service = EvidenceCollectionService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
        adapter_factory=_RetryingAdapterFactory(),
        clock=lambda: NOW,
        owner="worker-1",
    )

    result = service.process(TASK_ID)

    assert result.outcome == "FAILED"
    assert result.failed_count >= 1


def test_permanent_adapter_configuration_error_is_saved_as_failed_evidence(
    migrated_engine: Engine,
) -> None:
    _seed(migrated_engine)
    service = EvidenceCollectionService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
        adapter_factory=_PermanentFailureFactory(),
        clock=lambda: NOW,
        owner="worker-1",
    )

    result = service.process(TASK_ID)

    assert result.outcome == "FAILED"
    assert result.failed_count >= 1


def _seed(
    engine: Engine,
    *,
    add_source: bool = True,
    service_name: str | None = "checkout",
) -> None:
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
                service_name=service_name,
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
        if add_source:
            MonitoringDataSourceRepository(session).insert(
                _source("mds_88888888888888888888888888888888", "PROMETHEUS", {})
            )
        session.commit()


def _source(
    source_id: str,
    source_type: str,
    field_mapping: dict[str, str],
) -> MonitoringDataSourceRecord:
    return MonitoringDataSourceRecord(
        id=source_id,
        name=source_type,
        environment="testing",
        source_type=source_type,
        base_url=f"http://{source_type.casefold()}:8080",
        credential_env_key=None,
        field_mapping=field_mapping,
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
