from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.domain.alert_event_lifecycle import (
    AlertEventState,
    LifecycleContext,
    LifecycleDecision,
    LifecyclePolicy,
    decide_lifecycle,
)
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.alert_group_repository import (
    AlertGroupAggregate,
    AlertGroupRepository,
)
from incident_intelligence.persistence.models import AlertEventLifecycleJobRow, AlertGroupRow
from incident_intelligence.persistence.repositories import RecordRepositories
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_event_lifecycle_jobs import (
    AlertEventLifecycleJobNotRetryable,
    AlertEventLifecycleLease,
)


class AlertEventLifecycleResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(pattern=r"^alj_[0-9a-f]{32}$")
    alert_group_id: str = Field(pattern=r"^agr_[0-9a-f]{32}$")
    previous_state: str
    state: str
    reason_code: str


class AlertEventLifecycleService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[IdPrefix], str] = new_id,
        policy: LifecyclePolicy | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory
        self._policy = policy or LifecyclePolicy()

    def process(self, lease: AlertEventLifecycleLease) -> AlertEventLifecycleResult:
        now = self._clock().astimezone(UTC)
        with self._uow_factory() as uow:
            repository = _groups(uow)
            job = repository.find_lifecycle_job(lease.id, for_update=True)
            if (
                job is None
                or job.state != "LEASED"
                or job.lease_owner != lease.lease_owner
                or job.alert_group_id != lease.alert_group_id
                or job.action != lease.action
            ):
                raise AlertEventLifecycleJobNotRetryable()
            group = repository.find_group(job.alert_group_id, for_update=True)
            if group is None:
                raise RuntimeError("alert_event_lifecycle_group_not_found")
            aggregate = repository.aggregate_group(
                group.id,
                recent_since=now - timedelta(seconds=60),
            )
            if aggregate is None:
                raise RuntimeError("alert_event_lifecycle_group_has_no_members")

            previous_state = group.state
            decision = decide_lifecycle(
                LifecycleContext(
                    state=cast(AlertEventState, group.state),
                    active_count=aggregate.active_count,
                    now=now,
                    forming_until=group.forming_until,
                    observing_until=group.observing_until,
                    closed_at=group.closed_at,
                ),
                self._policy,
            )
            changed = _apply_decision(group, aggregate, decision, now)
            next_action = _next_action(decision)
            if next_action == job.action and decision.schedule_at is not None:
                _requeue(job, group.version, decision.schedule_at, now)
            else:
                _complete(job, now)
                if next_action is not None and decision.schedule_at is not None:
                    repository.schedule_lifecycle(
                        group,
                        action=next_action,
                        available_at=decision.schedule_at,
                        now=now,
                    )
            if changed:
                _records(uow).add_audit(
                    audit_id=self._id_factory("aud"),
                    actor="alert-event-lifecycle-worker",
                    action="alert_event.state_changed",
                    resource_type="alert_group",
                    resource_id=group.id,
                    request_id=job.id,
                    details={
                        "previous_state": previous_state,
                        "state": group.state,
                        "reason_code": decision.reason_code,
                    },
                    created_at=now,
                )
                if (
                    group.state == "ACTIVE"
                    and group.severity in {"critical", "high"}
                    and group.environment == "production"
                ):
                    repository.schedule_correlation(
                        group,
                        target_version=group.version,
                        now=now,
                    )
            repository.flush()
            uow.commit()
            return AlertEventLifecycleResult(
                job_id=job.id,
                alert_group_id=group.id,
                previous_state=previous_state,
                state=group.state,
                reason_code=decision.reason_code,
            )


def _apply_decision(
    group: AlertGroupRow,
    aggregate: AlertGroupAggregate,
    decision: LifecycleDecision,
    now: datetime,
) -> bool:
    previous = (
        group.state,
        group.forming_until,
        group.observing_until,
        group.closed_at,
        group.active_count,
    )
    group.state = decision.state
    group.forming_until = decision.forming_until
    group.observing_until = decision.observing_until
    group.closed_at = decision.closed_at
    group.active_count = aggregate.active_count
    group.total_count = aggregate.total_count
    group.impacted_resource_count = aggregate.impacted_resource_count
    group.first_observed_at = aggregate.first_observed_at
    group.last_observed_at = aggregate.last_observed_at
    group.last_member_at = aggregate.last_member_at
    group.severity = aggregate.representative_severity
    group.representative_alert_id = aggregate.representative_alert_id
    group.title = aggregate.representative_title
    current = (
        group.state,
        group.forming_until,
        group.observing_until,
        group.closed_at,
        group.active_count,
    )
    changed = current != previous
    if changed:
        group.state_changed_at = now
        group.version += 1
        group.reason_codes = [decision.reason_code]
        group.explanation = decision.explanation
        group.updated_at = now
    return changed


def _next_action(decision: LifecycleDecision) -> str | None:
    if decision.state == "FORMING":
        return "FORMING_COMPLETE"
    if decision.state == "OBSERVING":
        return "OBSERVATION_COMPLETE"
    return None


def _requeue(
    job: AlertEventLifecycleJobRow,
    target_group_version: int,
    available_at: datetime,
    now: datetime,
) -> None:
    job.target_group_version = target_group_version
    job.state = "PENDING"
    job.active_slot = 1
    job.attempts = 0
    job.available_at = available_at
    job.lease_owner = None
    job.lease_expires_at = None
    job.last_error_code = None
    job.updated_at = now


def _complete(job: AlertEventLifecycleJobRow, now: datetime) -> None:
    job.state = "SUCCEEDED"
    job.active_slot = None
    job.lease_owner = None
    job.lease_expires_at = None
    job.last_error_code = None
    job.updated_at = now


def _groups(uow: SqlAlchemyUnitOfWork) -> AlertGroupRepository:
    if uow.alert_groups is None:
        raise RuntimeError("工作单元没有可用告警组仓储")
    return uow.alert_groups


def _records(uow: SqlAlchemyUnitOfWork) -> RecordRepositories:
    if uow.records is None:
        raise RuntimeError("工作单元没有可用记录仓储")
    return uow.records
