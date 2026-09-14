from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import MetaData, Table
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from incident_intelligence.domain.diagnosis import (
    DiagnosisReference,
    DiagnosisRun,
    DiagnosisSnapshot,
)
from incident_intelligence.domain.evidence import (
    EvidenceContext,
    EvidenceRun,
    build_evidence_window,
)
from incident_intelligence.persistence.diagnosis_repository import (
    DiagnosisRunRepository,
    DiagnosisTaskRecord,
)
from incident_intelligence.persistence.evidence_repository import EvidenceRunRepository

NOW = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
INCIDENT_ID = "inc_11111111111111111111111111111111"
EVIDENCE_RUN_ID = "evr_22222222222222222222222222222222"
DIAGNOSIS_RUN_ID = "drun_33333333333333333333333333333333"
DIAGNOSIS_TASK_ID = "dtask_44444444444444444444444444444444"


def test_repository_persists_run_snapshot_and_pending_task_together(
    migrated_engine: Engine,
) -> None:
    _seed_parent_rows(migrated_engine)
    run = DiagnosisRun(
        id=DIAGNOSIS_RUN_ID,
        incident_id=INCIDENT_ID,
        evidence_run_id=EVIDENCE_RUN_ID,
        state="QUEUED",
        requested_by="operator",
        created_at=NOW,
    )
    snapshot = DiagnosisSnapshot(
        diagnosis_run_id=DIAGNOSIS_RUN_ID,
        incident_id=INCIDENT_ID,
        evidence_run_id=EVIDENCE_RUN_ID,
        environment="testing",
        service_name="checkout",
        alert_names=("HighErrorRate",),
        evidence_references=(
            DiagnosisReference(
                kind="EVIDENCE",
                target_id="evitem_55555555555555555555555555555555",
                content_hash="a" * 64,
            ),
        ),
        created_at=NOW,
    )
    task = DiagnosisTaskRecord(
        id=DIAGNOSIS_TASK_ID,
        diagnosis_run_id=DIAGNOSIS_RUN_ID,
        state="PENDING",
        attempt_count=0,
        next_attempt_at=NOW,
        lease_owner=None,
        lease_until=None,
        last_error_code=None,
        created_at=NOW,
        updated_at=NOW,
    )

    with Session(migrated_engine) as session:
        repository = DiagnosisRunRepository(session)
        repository.insert_run_with_snapshot_and_task(run, snapshot, task)
        session.commit()

    with Session(migrated_engine) as session:
        repository = DiagnosisRunRepository(session)
        assert repository.get(DIAGNOSIS_RUN_ID) == run
        assert repository.get_snapshot(DIAGNOSIS_RUN_ID) == snapshot
        persisted_task = repository.get_task(DIAGNOSIS_TASK_ID)
        assert persisted_task is not None
        assert persisted_task.state == "PENDING"
        assert persisted_task.diagnosis_run_id == DIAGNOSIS_RUN_ID


def _seed_parent_rows(engine: Engine) -> None:
    with Session(engine) as session:
        metadata = MetaData()
        rules = Table("incident_rules", metadata, autoload_with=session.bind)
        incidents = Table("operational_incidents", metadata, autoload_with=session.bind)
        session.execute(
            rules.insert().values(
                id="irl_66666666666666666666666666666666",
                name="诊断测试规则",
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
                reference="INC-20260914-001",
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
        EvidenceRunRepository(session).insert(
            EvidenceRun(
                id=EVIDENCE_RUN_ID,
                incident_id=INCIDENT_ID,
                trigger="AUTOMATIC",
                state="SUCCEEDED",
                anchor_at=NOW,
                window=build_evidence_window(NOW, NOW),
                context=EvidenceContext(
                    environment="testing",
                    service_name="checkout",
                    alert_names=("HighErrorRate",),
                ),
                requested_by="incident-evaluation",
                created_at=NOW,
                completed_at=NOW,
            )
        )
        session.commit()
