from __future__ import annotations

from datetime import UTC, datetime
from functools import partial

from sqlalchemy import func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from incident_intelligence.persistence.models import (
    AlertGroupMemberRow,
    AlertGroupRow,
    AlertRow,
    AuditEventRow,
    SignalEventRow,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_group_center import (
    AlertGroupCenterService,
    AlertGroupFilters,
)
from incident_intelligence.services.alert_regrouping import AlertRegroupingService
from tests.integration.persistence.test_constraints import (
    make_alert,
    make_alert_group,
    make_signal,
)

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)
PROBLEM_KEY = "4" * 64


def test_legacy_unlinked_groups_are_consolidated_without_deleting_history(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    group_ids: list[str] = []
    with session_factory.begin() as session:
        for index in range(3):
            signal = make_signal(
                source_event_id=f"{index + 1:064x}",
                service=None,
                entity_type="POD",
                entity_key=f"{index + 1:064x}",
                entity_display_name=f"payment-{index}",
                facts={
                    "alertname": "KubePodNotReady",
                    "namespace": "payments",
                    "pod": f"payment-{index}",
                },
            )
            session.add(signal)
            session.flush()
            alert = make_alert(
                signal.id,
                source_alert_key=f"pod-not-ready-{index}",
                source_instance=f"{index + 1:064x}",
                service=None,
                entity_type="POD",
                entity_key=f"{index + 1:064x}",
                entity_display_name=f"payment-{index}",
            )
            session.add(alert)
            session.flush()
            group = make_alert_group(
                alert.id,
                rule_version="alert-grouping.v1",
                service=None,
                entity_type="POD",
                entity_key=f"{index + 1:064x}",
                entity_display_name=f"payment-{index}",
                problem_key=PROBLEM_KEY,
                problem_type="KubePodNotReady",
                scope_type="NAMESPACE",
                scope_key="5" * 64,
                scope_display_name="payments",
            )
            session.add(group)
            session.flush()
            group_ids.append(group.id)
            session.add(
                AlertGroupMemberRow(
                    alert_group_id=group.id,
                    alert_id=alert.id,
                    alert_cycle=1,
                    joined_alert_version=1,
                    current_alert_version=1,
                    current_state="ACTIVE",
                    current_severity="high",
                    resource_type="pod",
                    resource_name=f"payment-{index}",
                    resource_key=f"{index + 1:064x}",
                    reason_code="no_eligible_group",
                    joined_at=NOW,
                    updated_at=NOW,
                )
            )

    service = AlertRegroupingService(
        uow_factory=partial(SqlAlchemyUnitOfWork, session_factory),
        clock=lambda: NOW,
    )
    result = service.regroup_active_unlinked_groups(
        limit=100,
        actor="local-admin",
        request_id="req-regroup-1",
    )
    replay = service.regroup_active_unlinked_groups(
        limit=100,
        actor="local-admin",
        request_id="req-regroup-1",
    )

    assert result.examined_groups == 3
    assert result.merged_groups == 2
    assert result.moved_members == 2
    assert replay.merged_groups == 0
    with session_factory.begin() as session:
        groups = list(session.scalars(select(AlertGroupRow).order_by(AlertGroupRow.id)))
        active = [group for group in groups if group.state == "ACTIVE"]
        resolved = [group for group in groups if group.state == "RESOLVED"]
        assert len(active) == 1
        assert len(resolved) == 2
        assert active[0].total_count == 3
        assert active[0].impacted_resource_count == 3
        assert active[0].rule_version == "alert-grouping.v2"
        assert all(group.reason_codes == ["regrouped_into_problem_signature"] for group in resolved)
        assert session.scalar(select(func.count()).select_from(AlertGroupMemberRow)) == 3
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 3
        for model in (SignalEventRow, AlertRow, AlertGroupRow):
            session.execute(
                update(model).where(model.service.is_(None)).values(service="test-cleanup")
            )

    visible = AlertGroupCenterService(session_factory=session_factory).list_groups(
        AlertGroupFilters()
    )
    assert visible.total == 1
    assert visible.items[0].total_count == 3
