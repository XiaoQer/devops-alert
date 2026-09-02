from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import MetaData, Table
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from incident_intelligence.domain.evidence import (
    EvidenceContext,
    EvidenceItem,
    EvidenceRun,
    build_evidence_window,
)
from incident_intelligence.persistence.evidence_repository import (
    EvidenceRunRepository,
    EvidenceTaskRecord,
    EvidenceTaskRepository,
)

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
INCIDENT_ID = "inc_11111111111111111111111111111111"
RUN_ID = "evr_22222222222222222222222222222222"


def test_automatic_run_is_unique_and_items_round_trip(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        _insert_incident(session)
        repository = EvidenceRunRepository(session)
        run = _run()
        repository.insert(run)
        repository.append_item(_item())
        session.commit()

        assert repository.get(RUN_ID) == run
        assert repository.list_items(RUN_ID) == (_item(),)

        with pytest.raises(IntegrityError):
            repository.insert(run.model_copy(update={"id": "evr_33333333333333333333333333333333"}))
            session.commit()


def test_task_claim_and_completion_require_current_lease_owner(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session:
        _insert_incident(session)
        EvidenceRunRepository(session).insert(_run())
        repository = EvidenceTaskRepository(session)
        task = EvidenceTaskRecord(
            id="evtask_44444444444444444444444444444444",
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
        repository.insert(task)

        assert repository.claim_due(
            task.id,
            owner="worker-1",
            now=NOW,
            lease_until=NOW + timedelta(seconds=30),
        )
        assert not repository.complete(task.id, owner="stale-worker", now=NOW)
        assert repository.complete(task.id, owner="worker-1", now=NOW)
        session.commit()

        completed = repository.get(task.id)
        assert completed is not None
        assert completed.state == "SUCCEEDED"
        assert completed.attempt_count == 1
        assert completed.lease_owner is None


def _run() -> EvidenceRun:
    return EvidenceRun(
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
            facts={"component": "mysql"},
        ),
        package_versions={"common-service": 1},
        requested_by="incident-evaluation",
        created_at=NOW,
    )


def _item() -> EvidenceItem:
    return EvidenceItem(
        id="evitem_55555555555555555555555555555555",
        evidence_run_id=RUN_ID,
        evidence_key="common-service.service-availability",
        display_name="服务可用性",
        source_type="PROMETHEUS",
        state="SUCCEEDED",
        package_id="common-service",
        package_version=1,
        template_id="prom.service.availability",
        template_version=1,
        evidence_type="METRIC_COMPARISON",
        query_started_at=NOW - timedelta(minutes=30),
        query_ended_at=NOW,
        step_seconds=15,
        query_parameters={"service": "checkout"},
        baseline_summary={"average": 1.0},
        fault_summary={"average": 0.5},
        interpretation="故障期可用性下降",
        normalized_result={"threshold_exceeded": True},
        created_at=NOW,
    )


def _insert_incident(session: Session) -> None:
    rules = Table("incident_rules", MetaData(), autoload_with=session.bind)
    incidents = Table("operational_incidents", MetaData(), autoload_with=session.bind)
    session.execute(
        rules.insert().values(
            id="irl_66666666666666666666666666666666",
            name="测试 Incident 规则",
            description="创建测试 Incident",
            state="PUBLISHED",
            environment="testing",
            alert_source_ids=[],
            services=["checkout"],
            group_by="SERVICE",
            window_minutes=5,
            conditions=[{"type": "ALERT_COUNT_GTE", "threshold": 1}],
            summary="测试规则",
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
