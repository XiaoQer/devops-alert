# ruff: noqa: RUF001

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.ids import new_id
from incident_intelligence.persistence.alert_group_repository import AlertGroupRepository
from incident_intelligence.persistence.models import AlertGroupRow
from incident_intelligence.persistence.repositories import RecordRepositories
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork

CURRENT_GROUPING_RULE_VERSION = "alert-grouping.v2"
REGROUP_REASON = "regrouped_into_problem_signature"


class AlertRegroupResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    examined_groups: int = Field(ge=0, le=100)
    merged_groups: int = Field(ge=0, le=99)
    moved_members: int = Field(ge=0, le=100)


class AlertRegroupingService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def regroup_active_unlinked_groups(
        self,
        *,
        limit: int,
        actor: str,
        request_id: str,
    ) -> AlertRegroupResult:
        if not 1 <= limit <= 100:
            raise ValueError("alert_regroup_limit_out_of_range")
        if not 1 <= len(actor) <= 128 or not 1 <= len(request_id) <= 64:
            raise ValueError("alert_regroup_audit_identity_invalid")
        now = self._clock().astimezone(UTC)
        with self._uow_factory() as uow:
            groups = _groups(uow)
            records = _records(uow)
            candidates = _bounded_candidates(groups, limit)
            by_problem: dict[str, list[AlertGroupRow]] = defaultdict(list)
            for group in candidates:
                by_problem[group.problem_key].append(group)

            merged_groups = 0
            moved_members = 0
            for problem_groups in by_problem.values():
                canonical = problem_groups[0]
                problem_moved_members = 0
                for source in problem_groups[1:]:
                    moved = groups.move_members(
                        source_group_id=source.id,
                        target_group_id=canonical.id,
                        now=now,
                    )
                    moved_members += moved
                    problem_moved_members += moved
                    merged_groups += 1
                    _resolve_source(source, canonical.id, now)
                    _audit(
                        records,
                        group=source,
                        actor=actor,
                        request_id=request_id,
                        target_group_id=canonical.id,
                        moved_members=moved,
                        now=now,
                    )

                _refresh_canonical(groups, canonical, now)
                groups.schedule_correlation(
                    canonical,
                    target_version=canonical.version,
                    now=now,
                )
                _audit(
                    records,
                    group=canonical,
                    actor=actor,
                    request_id=request_id,
                    target_group_id=canonical.id,
                    moved_members=problem_moved_members,
                    now=now,
                )

            groups.flush()
            uow.commit()
            return AlertRegroupResult(
                examined_groups=len(candidates),
                merged_groups=merged_groups,
                moved_members=moved_members,
            )


def _bounded_candidates(repository: AlertGroupRepository, limit: int) -> tuple[AlertGroupRow, ...]:
    selected: list[AlertGroupRow] = []
    member_count = 0
    for group in repository.legacy_regroup_candidates(limit=limit):
        count = len(repository.list_members(group.id))
        if count == 0 or member_count + count > limit:
            break
        selected.append(group)
        member_count += count
    return tuple(selected)


def _resolve_source(group: AlertGroupRow, target_group_id: str, now: datetime) -> None:
    group.state = "CLOSED"
    group.storm_state = "NORMAL"
    group.rule_version = CURRENT_GROUPING_RULE_VERSION
    group.active_count = 0
    group.state_changed_at = now
    group.reason_codes = [REGROUP_REASON]
    group.explanation = f"成员已安全迁入问题签名告警组 {target_group_id}，保留本组历史审计。"
    group.updated_at = now
    group.version += 1


def _refresh_canonical(
    repository: AlertGroupRepository, group: AlertGroupRow, now: datetime
) -> None:
    aggregate = repository.aggregate_group(
        group.id,
        recent_since=now - timedelta(seconds=60),
    )
    if aggregate is None:
        raise RuntimeError("alert_regroup_canonical_has_no_members")
    group.rule_version = CURRENT_GROUPING_RULE_VERSION
    group.state = "ACTIVE" if aggregate.active_count else "CLOSED"
    group.storm_state = "STORM" if aggregate.recent_member_count >= 20 else "NORMAL"
    group.title = aggregate.representative_title
    group.severity = aggregate.representative_severity
    group.representative_alert_id = aggregate.representative_alert_id
    group.first_observed_at = aggregate.first_observed_at
    group.last_observed_at = aggregate.last_observed_at
    group.last_member_at = aggregate.last_member_at
    group.active_count = aggregate.active_count
    group.total_count = aggregate.total_count
    group.impacted_resource_count = aggregate.impacted_resource_count
    group.state_changed_at = now
    group.reason_codes = [REGROUP_REASON]
    group.explanation = "旧告警组已按问题类型、环境和影响范围安全收敛。"
    group.updated_at = now
    group.version += 1


def _audit(
    records: RecordRepositories,
    *,
    group: AlertGroupRow,
    actor: str,
    request_id: str,
    target_group_id: str,
    moved_members: int,
    now: datetime,
) -> None:
    records.add_audit(
        audit_id=new_id("aud"),
        actor=actor,
        action="alert_group.regrouped",
        resource_type="alert_group",
        resource_id=group.id,
        request_id=request_id,
        details={
            "reason_code": REGROUP_REASON,
            "target_group_id": target_group_id,
            "moved_members": str(moved_members),
        },
        created_at=now,
    )


def _groups(uow: SqlAlchemyUnitOfWork) -> AlertGroupRepository:
    if uow.alert_groups is None:
        raise RuntimeError("工作单元没有可用告警组仓储")
    return uow.alert_groups


def _records(uow: SqlAlchemyUnitOfWork) -> RecordRepositories:
    if uow.records is None:
        raise RuntimeError("工作单元没有可用记录仓储")
    return uow.records
