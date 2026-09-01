from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from incident_intelligence.domain.alert_sources import ALERTMANAGER_COMPAT_SOURCE_ID
from incident_intelligence.domain.incident_rules import (
    IncidentRuleConfig,
    create_rule,
    publish_rule,
    record_successful_dry_run,
)
from incident_intelligence.persistence.incident_repository import (
    IncidentEvaluationJobRecord,
    IncidentEvaluationJobRepository,
    IncidentRepository,
)
from incident_intelligence.persistence.incident_rule_repository import IncidentRuleRepository
from incident_intelligence.persistence.models import (
    AlertLifecycleRow,
    IncidentEvaluationJobRow,
    IncidentNotificationOutboxRow,
    OperationalIncidentActivityRow,
    OperationalIncidentAlertRow,
    OperationalIncidentRow,
)
from incident_intelligence.persistence.session import make_session_factory
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.incident_evaluation import IncidentEvaluationService

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
RULE_ID = "irl_11111111111111111111111111111111"
JOB_ID = "iej_22222222222222222222222222222222"


def test_published_rule_hit_creates_formal_incident_and_links_window_alerts(
    migrated_engine: Engine,
) -> None:
    _seed(migrated_engine, published=True)
    ids = iter(
        (
            "inc_33333333333333333333333333333333",
            "iact_44444444444444444444444444444444",
            "ino_99999999999999999999999999999999",
        )
    )
    service = IncidentEvaluationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
        clock=lambda: NOW,
        id_factory=lambda prefix: next(ids),
        reference_factory=lambda now: "INC-20260901-001",
    )

    result = service.process(JOB_ID)

    assert result.outcome == "INCIDENT_CREATED"
    assert result.reason_codes == ("published_rule_matched",)
    assert result.incident_ids == ("inc_33333333333333333333333333333333",)
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(OperationalIncidentRow)) == 1
        linked = tuple(
            session.scalars(
                select(OperationalIncidentAlertRow.alert_id).order_by(
                    OperationalIncidentAlertRow.alert_id
                )
            )
        )
        assert linked == (
            "alt_00000000000000000000000000000001",
            "alt_00000000000000000000000000000002",
        )
        job = session.get(IncidentEvaluationJobRow, JOB_ID)
        assert job is not None and job.state == "SUCCEEDED"
        notification = session.scalar(select(IncidentNotificationOutboxRow))
        assert notification is not None
        assert notification.incident_id == result.incident_ids[0]
        assert notification.kind == "CREATE_CARD"
        assert notification.state == "PENDING"


def test_draft_rule_is_not_evaluated(migrated_engine: Engine) -> None:
    _seed(migrated_engine, published=False)
    service = IncidentEvaluationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
        clock=lambda: NOW,
    )

    result = service.process(JOB_ID)

    assert result.outcome == "NO_MATCH"
    assert result.reason_codes == ("no_published_rule_match",)
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(OperationalIncidentRow)) == 0


def test_two_matching_jobs_converge_to_one_unresolved_incident(
    migrated_engine: Engine,
    monkeypatch,
) -> None:
    _seed(migrated_engine, published=True, same_received_at=True)
    second_job_id = "iej_66666666666666666666666666666666"
    with Session(migrated_engine) as session:
        _enqueue_job(
            session,
            job_id=second_job_id,
            alert_id="alt_00000000000000000000000000000001",
            alert_version=1,
        )
        session.commit()

    barrier = Barrier(2)
    lock = Lock()
    empty_reads = 0
    original_find = IncidentRepository.find_unresolved

    def synchronized_find(self, **kwargs):
        nonlocal empty_reads
        result = original_find(self, **kwargs)
        if result is None:
            with lock:
                empty_reads += 1
                should_wait = empty_reads <= 2
            if should_wait:
                barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(IncidentRepository, "find_unresolved", synchronized_find)
    service = IncidentEvaluationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
        clock=lambda: NOW,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(service.process, (JOB_ID, second_job_id)))

    assert {result.outcome for result in results} <= {
        "INCIDENT_CREATED",
        "INCIDENT_UPDATED",
    }
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(OperationalIncidentRow)) == 1
        assert session.scalar(select(func.count()).select_from(OperationalIncidentAlertRow)) == 2


def test_all_linked_alerts_recovered_records_once_without_resolving_incident(
    migrated_engine: Engine,
) -> None:
    _seed(migrated_engine, published=True)
    service = IncidentEvaluationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
        clock=lambda: NOW,
    )
    created = service.process(JOB_ID)
    incident_id = created.incident_ids[0]

    recovery_jobs = (
        "iej_77777777777777777777777777777777",
        "iej_88888888888888888888888888888888",
    )
    with Session(migrated_engine) as session:
        alerts = tuple(session.scalars(select(AlertLifecycleRow).order_by(AlertLifecycleRow.id)))
        for alert, job_id in zip(alerts, recovery_jobs, strict=True):
            alert.state = "RESOLVED"
            alert.resolved_at = NOW + timedelta(minutes=1)
            alert.version = 2
            alert.updated_at = NOW + timedelta(minutes=1)
            _enqueue_job(
                session,
                job_id=job_id,
                alert_id=alert.id,
                alert_version=2,
                available_at=NOW + timedelta(minutes=1),
            )
        session.commit()

    later_service = IncidentEvaluationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
        clock=lambda: NOW + timedelta(minutes=1),
    )
    for job_id in recovery_jobs:
        later_service.process(job_id)

    with Session(migrated_engine) as session:
        incident = session.get(OperationalIncidentRow, incident_id)
        assert incident is not None
        assert incident.state == "OPEN"
        assert incident.active_alert_count == 0
        recovered_count = session.scalar(
            select(func.count())
            .select_from(OperationalIncidentActivityRow)
            .where(
                OperationalIncidentActivityRow.incident_id == incident_id,
                OperationalIncidentActivityRow.kind == "ALL_ALERTS_RECOVERED",
            )
        )
        assert recovered_count == 1


def _seed(
    engine: Engine,
    *,
    published: bool,
    same_received_at: bool = False,
) -> None:
    with Session(engine) as session:
        rule = create_rule(
            rule_id=RULE_ID,
            name="支付链路异常",
            description="同一服务出现两类告警时创建 Incident",
            config=IncidentRuleConfig.model_validate(
                {
                    "environment": "production",
                    "alert_source_ids": (),
                    "services": ("checkout",),
                    "group_by": "SERVICE",
                    "window_minutes": 5,
                    "conditions": ({"type": "DISTINCT_ALERT_NAMES_GTE", "threshold": 2},),
                }
            ),
            now=NOW - timedelta(minutes=10),
        )
        if published:
            rule = record_successful_dry_run(
                rule,
                dry_run_id="ird_55555555555555555555555555555555",
                now=NOW - timedelta(minutes=9),
            )
            rule = publish_rule(rule, now=NOW - timedelta(minutes=8))
        IncidentRuleRepository(session).insert(rule)
        first_received = NOW if same_received_at else NOW - timedelta(minutes=2)
        alerts = (_alert(1, "HighLatency", first_received), _alert(2, "ErrorRate", NOW))
        session.add_all(alerts)
        _enqueue_job(
            session,
            job_id=JOB_ID,
            alert_id=alerts[-1].id,
            alert_version=1,
        )
        session.commit()


def _enqueue_job(
    session: Session,
    *,
    job_id: str,
    alert_id: str,
    alert_version: int,
    available_at: datetime = NOW,
) -> None:
    IncidentEvaluationJobRepository(session).enqueue(
        IncidentEvaluationJobRecord(
            id=job_id,
            alert_id=alert_id,
            alert_version=alert_version,
            state="PENDING",
            attempt_count=0,
            available_at=available_at,
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=None,
            outcome=None,
            reason_codes=(),
            incident_ids=(),
            created_at=available_at,
            updated_at=available_at,
        )
    )


def _alert(index: int, name: str, received_at: datetime) -> AlertLifecycleRow:
    return AlertLifecycleRow(
        id=f"alt_{index:032x}",
        alert_source_id=ALERTMANAGER_COMPAT_SOURCE_ID,
        source_alert_key=f"checkout-{index}",
        episode_started_at=received_at,
        alert_name=name,
        summary=name,
        description="测试告警",
        state="ACTIVE",
        severity="high",
        environment="production",
        service="checkout",
        entity_type="SERVICE",
        entity_key="a" * 64,
        entity_display_name="checkout",
        first_observed_at=received_at,
        last_observed_at=received_at,
        first_received_at=received_at,
        last_received_at=received_at,
        resolved_at=None,
        firing_observed=True,
        version=1,
        created_at=received_at,
        updated_at=received_at,
    )
