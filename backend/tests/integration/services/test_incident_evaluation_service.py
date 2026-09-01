from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
)
from incident_intelligence.persistence.incident_rule_repository import IncidentRuleRepository
from incident_intelligence.persistence.models import (
    AlertLifecycleRow,
    IncidentEvaluationJobRow,
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


def _seed(engine: Engine, *, published: bool) -> None:
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
                    "conditions": (
                        {"type": "DISTINCT_ALERT_NAMES_GTE", "threshold": 2},
                    ),
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
        alerts = (_alert(1, "HighLatency", NOW - timedelta(minutes=2)), _alert(2, "ErrorRate", NOW))
        session.add_all(alerts)
        IncidentEvaluationJobRepository(session).enqueue(
            IncidentEvaluationJobRecord(
                id=JOB_ID,
                alert_id=alerts[-1].id,
                alert_version=1,
                state="PENDING",
                attempt_count=0,
                available_at=NOW,
                lease_owner=None,
                lease_expires_at=None,
                last_error_code=None,
                outcome=None,
                reason_codes=(),
                incident_ids=(),
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.commit()


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
