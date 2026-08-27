from __future__ import annotations

from datetime import UTC, datetime
from functools import partial

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.alert_event_operations import (
    ConfirmAlertEventMemberCommand,
    MergeAlertEventsCommand,
    SplitAlertEventMembersCommand,
)
from incident_intelligence.ids import new_id
from incident_intelligence.persistence.models import (
    AlertEventMembershipDecisionRow,
    AlertEventOperationRow,
    AlertEventProfileRow,
    AlertGroupMemberRow,
    AlertGroupRow,
    AlertRow,
    AuditEventRow,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_event_operations import (
    AlertEventEnvironmentConflict,
    AlertEventOperationConflict,
    AlertEventOperationService,
    AlertEventVersionConflict,
)
from incident_intelligence.services.alert_grouping import AlertGroupingService
from incident_intelligence.services.alert_grouping_jobs import AlertGroupingJobService
from incident_intelligence.services.signal_intake import SignalIntakeService
from tests.integration.services.test_alert_grouping_service import (
    command,
    seed_catalog,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def build_services(
    session_factory: sessionmaker[Session], clock: list[datetime]
) -> tuple[
    SignalIntakeService,
    AlertGroupingJobService,
    AlertGroupingService,
    AlertEventOperationService,
]:
    uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
    return (
        SignalIntakeService(uow_factory=uow_factory, clock=lambda: clock[0]),
        AlertGroupingJobService(uow_factory=uow_factory),
        AlertGroupingService(uow_factory=uow_factory, clock=lambda: clock[0]),
        AlertEventOperationService(session_factory=session_factory, clock=lambda: clock[0]),
    )


def process_all_grouping(
    jobs: AlertGroupingJobService, grouping: AlertGroupingService, now: datetime
) -> None:
    while leases := jobs.claim_batch("grouping", now, limit=50, lease_seconds=30):
        for lease in leases:
            grouping.process(lease)


def test_confirm_pending_member_preserves_pending_decision_and_is_idempotent(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    seed_catalog(session_factory)
    clock = [NOW]
    intake, jobs, grouping, operations = build_services(session_factory, clock)
    intake.submit_batch([command(1)], "alertmanager-adapter", "req-group")
    process_all_grouping(jobs, grouping, clock[0])
    intake.submit_batch(
        [command(2, service="inventory-api")],
        "alertmanager-adapter",
        "req-pending",
    )
    with session_factory.begin() as session:
        group = session.scalar(select(AlertGroupRow))
        alert = session.scalar(select(AlertRow).where(AlertRow.service == "inventory-api"))
        assert group is not None and alert is not None
        group.pending_count = 1
        session.add(
            AlertEventMembershipDecisionRow(
                id=new_id("amd"),
                alert_id=alert.id,
                alert_cycle=alert.cycle,
                alert_version=alert.version,
                state="PENDING",
                candidate_group_ids=[group.id],
                selected_group_id=None,
                selected_group_version=None,
                rule_version="alert-event-clustering.v1",
                scores={},
                reason_codes=["medium_confidence_event_match"],
                explanation="需要人工确认。",
                created_at=clock[0],
            )
        )
        group_id = group.id
        alert_id = alert.id
        expected_version = group.version

    command_value = ConfirmAlertEventMemberCommand(
        expected_version=expected_version,
        reason="已核对调用链和发生时间",
    )
    first = operations.confirm_member(
        group_id,
        alert_id,
        command_value,
        actor="manual-api-client",
        idempotency_key="confirm-1",
        request_id="req-confirm-1",
    )
    replay = operations.confirm_member(
        group_id,
        alert_id,
        command_value,
        actor="manual-api-client",
        idempotency_key="confirm-1",
        request_id="req-confirm-replay",
    )

    assert replay == first
    assert first.kind == "CONFIRM"
    assert first.moved_alert_ids == (alert_id,)
    with session_factory() as session:
        member = session.scalar(
            select(AlertGroupMemberRow).where(AlertGroupMemberRow.alert_id == alert_id)
        )
        assert member is not None and member.alert_group_id == group_id
        group = session.get(AlertGroupRow, group_id)
        assert group is not None and group.pending_count == 0 and group.total_count == 2
        states = tuple(
            session.scalars(
                select(AlertEventMembershipDecisionRow.state)
                .where(AlertEventMembershipDecisionRow.alert_id == alert_id)
                .order_by(AlertEventMembershipDecisionRow.created_at)
            )
        )
        assert sorted(states) == ["MANUAL_CONFIRMED", "PENDING"]
        assert session.scalar(select(func.count()).select_from(AlertEventOperationRow)) == 1


def test_split_moves_selected_member_and_keeps_immutable_membership_history(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    seed_catalog(session_factory)
    clock = [NOW]
    intake, jobs, grouping, operations = build_services(session_factory, clock)
    intake.submit_batch(
        [command(1), command(2), command(3)],
        "alertmanager-adapter",
        "req-group",
    )
    process_all_grouping(jobs, grouping, clock[0])
    with session_factory() as session:
        group = session.scalar(select(AlertGroupRow))
        selected = session.scalar(
            select(AlertRow).where(AlertRow.source_alert_key == "payment-error-3")
        )
        assert group is not None and selected is not None
        source_group_id = group.id
        selected_id = selected.id
        expected_version = group.version
        decisions_before = int(
            session.scalar(select(func.count()).select_from(AlertEventMembershipDecisionRow)) or 0
        )

    result = operations.split_members(
        source_group_id,
        SplitAlertEventMembersCommand(
            expected_version=expected_version,
            alert_ids=(selected_id,),
            reason="该实例属于独立故障批次",
        ),
        actor="manual-api-client",
        idempotency_key="split-1",
        request_id="req-split-1",
    )

    assert result.target_group_id != source_group_id
    assert result.moved_alert_ids == (selected_id,)
    with session_factory() as session:
        source = session.get(AlertGroupRow, source_group_id)
        target = session.get(AlertGroupRow, result.target_group_id)
        assert source is not None and source.total_count == 2
        assert target is not None and target.total_count == 1
        moved = session.scalar(
            select(AlertGroupMemberRow).where(AlertGroupMemberRow.alert_id == selected_id)
        )
        assert moved is not None and moved.alert_group_id == target.id
        assert (
            session.scalar(select(func.count()).select_from(AlertEventMembershipDecisionRow))
            == decisions_before + 2
        )
        manual_states = tuple(
            session.scalars(
                select(AlertEventMembershipDecisionRow.state).where(
                    AlertEventMembershipDecisionRow.alert_id == selected_id,
                    AlertEventMembershipDecisionRow.state.in_(("REMOVED", "MANUAL_CONFIRMED")),
                )
            )
        )
        assert sorted(manual_states) == ["MANUAL_CONFIRMED", "REMOVED"]
        assert session.scalar(select(func.count()).select_from(AlertEventProfileRow)) >= 5
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) >= 4


def test_merge_moves_members_once_and_rejects_changed_idempotent_command(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    seed_catalog(session_factory)
    clock = [NOW]
    intake, jobs, grouping, operations = build_services(session_factory, clock)
    intake.submit_batch(
        [command(1), command(2, service="inventory-api", title="库存接口错误率升高")],
        "alertmanager-adapter",
        "req-groups",
    )
    process_all_grouping(jobs, grouping, clock[0])
    with session_factory() as session:
        groups = tuple(session.scalars(select(AlertGroupRow).order_by(AlertGroupRow.service)))
        assert len(groups) == 2
        target, source = groups
        target_id = target.id
        source_id = source.id
        expected_version = target.version

    merge_command = MergeAlertEventsCommand(
        expected_version=expected_version,
        source_group_id=source_id,
        reason="同一业务故障的跨服务传播",
    )
    first = operations.merge_events(
        target_id,
        merge_command,
        actor="manual-api-client",
        idempotency_key="merge-1",
        request_id="req-merge-1",
    )
    replay = operations.merge_events(
        target_id,
        merge_command,
        actor="manual-api-client",
        idempotency_key="merge-1",
        request_id="req-merge-replay",
    )
    assert replay == first

    with pytest.raises(AlertEventOperationConflict):
        operations.merge_events(
            target_id,
            merge_command.model_copy(update={"reason": "修改后的理由"}),
            actor="manual-api-client",
            idempotency_key="merge-1",
            request_id="req-merge-conflict",
        )

    with session_factory() as session:
        target = session.get(AlertGroupRow, target_id)
        source = session.get(AlertGroupRow, source_id)
        assert target is not None and target.total_count == 2
        assert source is not None and source.state == "CLOSED"
        assert (
            session.scalar(
                select(func.count())
                .select_from(AlertGroupMemberRow)
                .where(AlertGroupMemberRow.alert_group_id == source_id)
            )
            == 0
        )
        assert session.scalar(select(func.count()).select_from(AlertEventOperationRow)) == 1


def test_merge_rejects_cross_environment_and_stale_version_without_partial_writes(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    seed_catalog(session_factory)
    clock = [NOW]
    intake, jobs, grouping, operations = build_services(session_factory, clock)
    intake.submit_batch(
        [command(1), command(2, environment="development")],
        "alertmanager-adapter",
        "req-groups",
    )
    process_all_grouping(jobs, grouping, clock[0])
    with session_factory() as session:
        production = session.scalar(
            select(AlertGroupRow).where(AlertGroupRow.environment == "production")
        )
        development = session.scalar(
            select(AlertGroupRow).where(AlertGroupRow.environment == "development")
        )
        assert production is not None and development is not None

    with pytest.raises(AlertEventEnvironmentConflict):
        operations.merge_events(
            production.id,
            MergeAlertEventsCommand(
                expected_version=production.version,
                source_group_id=development.id,
                reason="错误的跨环境合并",
            ),
            actor="manual-api-client",
            idempotency_key="merge-cross-env",
            request_id="req-cross-env",
        )
    with pytest.raises(AlertEventVersionConflict):
        operations.split_members(
            production.id,
            SplitAlertEventMembersCommand(
                expected_version=production.version + 1,
                alert_ids=(production.representative_alert_id,),
                reason="旧页面操作",
            ),
            actor="manual-api-client",
            idempotency_key="split-stale",
            request_id="req-stale",
        )

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AlertEventOperationRow)) == 0
        assert session.scalar(select(func.count()).select_from(AlertGroupMemberRow)) == 2
