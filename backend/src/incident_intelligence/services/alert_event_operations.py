from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.alert_event_operations import (
    AlertEventOperationRuleError,
    ConfirmAlertEventMemberCommand,
    MergeAlertEventsCommand,
    SplitAlertEventMembersCommand,
    require_merge_allowed,
    require_split_allowed,
)
from incident_intelligence.domain.alert_grouping import derive_resource_identity
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.alert_group_repository import AlertGroupRepository
from incident_intelligence.persistence.models import (
    AlertEventMembershipDecisionRow,
    AlertEventOperationRow,
    AlertGroupMemberRow,
    AlertGroupRow,
    AlertRow,
    SignalEventRow,
)
from incident_intelligence.persistence.repositories import RecordRepositories
from incident_intelligence.services.alert_group_center import AlertGroupResourceNotFound
from incident_intelligence.services.alert_grouping import (
    GROUPING_RULE_VERSION,
    refresh_group_membership,
    save_current_event_profile,
    schedule_event_lifecycle,
)

OperationKind = Literal["CONFIRM", "SPLIT", "MERGE"]


class AlertEventOperationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_id: str
    kind: OperationKind
    source_group_id: str
    target_group_id: str
    source_version: int
    target_version: int
    moved_alert_ids: tuple[str, ...]
    occurred_at: datetime


class AlertEventOperationError(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class AlertEventVersionConflict(AlertEventOperationError):
    def __init__(self) -> None:
        super().__init__("alert_event_version_conflict")


class AlertEventOperationConflict(AlertEventOperationError):
    def __init__(self) -> None:
        super().__init__("alert_event_operation_conflict")


class AlertEventEnvironmentConflict(AlertEventOperationError):
    def __init__(self) -> None:
        super().__init__("alert_event_environment_conflict")


class AlertEventIncidentConflict(AlertEventOperationError):
    def __init__(self) -> None:
        super().__init__("alert_event_incident_conflict")


class AlertEventMemberConflict(AlertEventOperationError):
    def __init__(self, reason_code: str = "alert_event_member_conflict") -> None:
        super().__init__(reason_code)


class AlertEventCapacityConflict(AlertEventOperationError):
    def __init__(self) -> None:
        super().__init__("alert_event_member_limit_exceeded")


@dataclass(frozen=True, slots=True)
class _MutationResult:
    source_group_id: str
    target_group_id: str
    source_version: int
    target_version: int
    moved_alert_ids: tuple[str, ...]
    before_versions: dict[str, int]
    after_versions: dict[str, int]


class AlertEventOperationService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[IdPrefix], str] = new_id,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory

    def confirm_member(
        self,
        group_id: str,
        alert_id: str,
        command: ConfirmAlertEventMemberCommand,
        *,
        actor: str,
        idempotency_key: str,
        request_id: str,
    ) -> AlertEventOperationResult:
        payload = {
            "group_id": group_id,
            "alert_id": alert_id,
            **command.model_dump(mode="json"),
        }
        return self._execute(
            kind="CONFIRM",
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=request_id,
            payload=payload,
            mutate=lambda session, now: self._confirm(
                session, group_id, alert_id, command, actor, request_id, now
            ),
        )

    def split_members(
        self,
        group_id: str,
        command: SplitAlertEventMembersCommand,
        *,
        actor: str,
        idempotency_key: str,
        request_id: str,
    ) -> AlertEventOperationResult:
        payload = {"group_id": group_id, **command.model_dump(mode="json")}
        return self._execute(
            kind="SPLIT",
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=request_id,
            payload=payload,
            mutate=lambda session, now: self._split(
                session, group_id, command, actor, request_id, now
            ),
        )

    def merge_events(
        self,
        target_group_id: str,
        command: MergeAlertEventsCommand,
        *,
        actor: str,
        idempotency_key: str,
        request_id: str,
    ) -> AlertEventOperationResult:
        payload = {"target_group_id": target_group_id, **command.model_dump(mode="json")}
        return self._execute(
            kind="MERGE",
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=request_id,
            payload=payload,
            mutate=lambda session, now: self._merge(
                session, target_group_id, command, actor, request_id, now
            ),
        )

    def _execute(
        self,
        *,
        kind: OperationKind,
        actor: str,
        idempotency_key: str,
        request_id: str,
        payload: dict[str, object],
        mutate: Callable[[Session, datetime], _MutationResult],
    ) -> AlertEventOperationResult:
        _validate_metadata(actor, idempotency_key, request_id)
        scope = f"alert_event.operation.{kind.casefold()}"
        key_hash = sha256(idempotency_key.encode()).hexdigest()
        fingerprint = _fingerprint(kind, actor, payload)
        try:
            with self._session_factory.begin() as session:
                existing = _find_operation(session, scope, key_hash)
                if existing is not None:
                    return _replay(existing, fingerprint)
                now = self._clock().astimezone(UTC)
                mutation = mutate(session, now)
                operation_id = self._id_factory("aeo")
                result = AlertEventOperationResult(
                    operation_id=operation_id,
                    kind=kind,
                    source_group_id=mutation.source_group_id,
                    target_group_id=mutation.target_group_id,
                    source_version=mutation.source_version,
                    target_version=mutation.target_version,
                    moved_alert_ids=mutation.moved_alert_ids,
                    occurred_at=now,
                )
                session.add(
                    AlertEventOperationRow(
                        id=operation_id,
                        scope=scope,
                        idempotency_key_hash=key_hash,
                        command_fingerprint=fingerprint,
                        kind=kind,
                        actor=actor,
                        source_group_id=mutation.source_group_id,
                        target_group_id=mutation.target_group_id,
                        before_versions=mutation.before_versions,
                        after_versions=mutation.after_versions,
                        result=result.model_dump(mode="json"),
                        created_at=now,
                    )
                )
                session.flush()
                return result
        except IntegrityError:
            replay = self._load_replay(scope, key_hash, fingerprint)
            if replay is None:
                raise
            return replay

    def _confirm(
        self,
        session: Session,
        group_id: str,
        alert_id: str,
        command: ConfirmAlertEventMemberCommand,
        actor: str,
        request_id: str,
        now: datetime,
    ) -> _MutationResult:
        repository = AlertGroupRepository(session)
        group = repository.find_group(group_id, for_update=True)
        if group is None:
            raise AlertGroupResourceNotFound()
        if group.version != command.expected_version:
            raise AlertEventVersionConflict()
        alert = session.scalar(select(AlertRow).where(AlertRow.id == alert_id).with_for_update())
        if alert is None:
            raise AlertEventMemberConflict("alert_event_alert_not_found")
        if repository.find_member(alert.id, alert.cycle, for_update=True) is not None:
            raise AlertEventMemberConflict("alert_event_member_already_confirmed")
        pending = _find_pending_decision(session, alert, group.id)
        if pending is None:
            raise AlertEventMemberConflict("alert_event_pending_decision_not_found")
        if alert.environment != group.environment:
            raise AlertEventEnvironmentConflict()
        if group.total_count >= group.member_limit:
            raise AlertEventCapacityConflict()
        signal = session.get(SignalEventRow, alert.signal_event_id)
        if signal is None:
            raise AlertEventMemberConflict("alert_event_signal_not_found")
        identity = derive_resource_identity(signal.facts, alert.id)
        before_version = group.version
        repository.add_member(
            AlertGroupMemberRow(
                alert_group_id=group.id,
                alert_id=alert.id,
                alert_cycle=alert.cycle,
                joined_alert_version=alert.version,
                current_alert_version=alert.version,
                current_state=alert.state,
                current_severity=alert.severity,
                resource_type=identity.resource_type,
                resource_name=identity.resource_name,
                resource_key=identity.resource_key,
                reason_code="manually_confirmed_membership",
                joined_at=now,
                updated_at=now,
            )
        )
        group.pending_count = max(0, group.pending_count - 1)
        refresh_group_membership(
            repository,
            group,
            reason_codes=["manually_confirmed_membership"],
            explanation="操作员确认该告警属于当前事件。",
            now=now,
        )
        _add_decision(
            session,
            decision_id=self._id_factory("amd"),
            alert=alert,
            state="MANUAL_CONFIRMED",
            candidate_group_ids=tuple(pending.candidate_group_ids),
            selected_group_id=group.id,
            selected_group_version=group.version,
            reason="manually_confirmed_membership",
            explanation=command.reason,
            now=now,
        )
        group.profile_version += 1
        save_current_event_profile(repository, group, now=now)
        schedule_event_lifecycle(repository, group, now=now)
        repository.schedule_correlation(group, target_version=group.version, now=now)
        _audit(
            session,
            self._id_factory("aud"),
            actor,
            "alert_event.member_confirmed",
            group.id,
            request_id,
            "manual_membership_confirmed",
            (alert.id,),
            now,
        )
        return _MutationResult(
            source_group_id=group.id,
            target_group_id=group.id,
            source_version=group.version,
            target_version=group.version,
            moved_alert_ids=(alert.id,),
            before_versions={group.id: before_version},
            after_versions={group.id: group.version},
        )

    def _split(
        self,
        session: Session,
        source_group_id: str,
        command: SplitAlertEventMembersCommand,
        actor: str,
        request_id: str,
        now: datetime,
    ) -> _MutationResult:
        repository = AlertGroupRepository(session)
        source = repository.find_group(source_group_id, for_update=True)
        if source is None:
            raise AlertGroupResourceNotFound()
        if source.version != command.expected_version:
            raise AlertEventVersionConflict()
        members = tuple(
            session.scalars(
                select(AlertGroupMemberRow)
                .where(
                    AlertGroupMemberRow.alert_group_id == source.id,
                    AlertGroupMemberRow.alert_id.in_(command.alert_ids),
                )
                .order_by(AlertGroupMemberRow.alert_id)
                .with_for_update()
            )
        )
        if len(members) != len(command.alert_ids):
            raise AlertEventMemberConflict("alert_event_member_not_found")
        try:
            require_split_allowed(
                total_count=source.total_count,
                selected_count=len(members),
            )
        except AlertEventOperationRuleError as error:
            raise _translate_rule(error) from error
        before_source_version = source.version
        target = _new_split_group(
            group_id=self._id_factory("agr"),
            source=source,
            representative_alert_id=members[0].alert_id,
            reason=command.reason,
            now=now,
        )
        repository.add_group(target)
        for member in members:
            member.alert_group_id = target.id
            member.reason_code = "manually_split_membership"
            member.updated_at = now
        repository.flush()
        refresh_group_membership(
            repository,
            source,
            reason_codes=["members_manually_split"],
            explanation="操作员已将部分告警拆分为独立事件。",
            now=now,
        )
        refresh_group_membership(
            repository,
            target,
            reason_codes=["created_by_manual_split"],
            explanation=command.reason,
            now=now,
        )
        alerts = _alerts_by_id(session, tuple(member.alert_id for member in members))
        for member in members:
            alert = alerts[member.alert_id]
            _add_decision(
                session,
                decision_id=self._id_factory("amd"),
                alert=alert,
                state="REMOVED",
                candidate_group_ids=(source.id,),
                selected_group_id=source.id,
                selected_group_version=before_source_version,
                reason="manually_removed_membership",
                explanation=command.reason,
                now=now,
            )
            _add_decision(
                session,
                decision_id=self._id_factory("amd"),
                alert=alert,
                state="MANUAL_CONFIRMED",
                candidate_group_ids=(target.id,),
                selected_group_id=target.id,
                selected_group_version=target.version,
                reason="manually_split_membership",
                explanation=command.reason,
                now=now,
            )
        source.profile_version += 1
        save_current_event_profile(repository, source, now=now)
        save_current_event_profile(repository, target, now=now)
        schedule_event_lifecycle(repository, source, now=now)
        schedule_event_lifecycle(repository, target, now=now)
        repository.schedule_correlation(source, target_version=source.version, now=now)
        repository.schedule_correlation(target, target_version=target.version, now=now)
        moved_ids = tuple(sorted(member.alert_id for member in members))
        _audit(
            session,
            self._id_factory("aud"),
            actor,
            "alert_event.members_split",
            source.id,
            request_id,
            "manual_event_split",
            moved_ids,
            now,
            target_group_id=target.id,
        )
        return _MutationResult(
            source_group_id=source.id,
            target_group_id=target.id,
            source_version=source.version,
            target_version=target.version,
            moved_alert_ids=moved_ids,
            before_versions={source.id: before_source_version},
            after_versions={source.id: source.version, target.id: target.version},
        )

    def _merge(
        self,
        session: Session,
        target_group_id: str,
        command: MergeAlertEventsCommand,
        actor: str,
        request_id: str,
        now: datetime,
    ) -> _MutationResult:
        groups = tuple(
            session.scalars(
                select(AlertGroupRow)
                .where(AlertGroupRow.id.in_((target_group_id, command.source_group_id)))
                .order_by(AlertGroupRow.id)
                .with_for_update()
            )
        )
        by_id = {group.id: group for group in groups}
        target = by_id.get(target_group_id)
        source = by_id.get(command.source_group_id)
        if target is None or source is None:
            raise AlertGroupResourceNotFound()
        if target.version != command.expected_version:
            raise AlertEventVersionConflict()
        try:
            require_merge_allowed(
                target_group_id=target.id,
                source_group_id=source.id,
                target_environment=target.environment,
                source_environment=source.environment,
                combined_member_count=target.total_count + source.total_count,
                member_limit=target.member_limit,
                target_incident_id=target.incident_id,
                source_incident_id=source.incident_id,
            )
        except AlertEventOperationRuleError as error:
            raise _translate_rule(error) from error
        repository = AlertGroupRepository(session)
        members = tuple(
            session.scalars(
                select(AlertGroupMemberRow)
                .where(AlertGroupMemberRow.alert_group_id == source.id)
                .order_by(AlertGroupMemberRow.alert_id)
                .with_for_update()
            )
        )
        if not members:
            raise AlertEventMemberConflict("alert_event_source_has_no_members")
        before_target_version = target.version
        before_source_version = source.version
        if target.incident_id is None:
            target.incident_id = source.incident_id
        for member in members:
            member.alert_group_id = target.id
            member.reason_code = "manually_merged_membership"
            member.updated_at = now
        repository.flush()
        refresh_group_membership(
            repository,
            target,
            reason_codes=["events_manually_merged"],
            explanation=command.reason,
            now=now,
        )
        source.state = "CLOSED"
        source.active_count = 0
        source.forming_until = None
        source.observing_until = None
        source.closed_at = now
        source.state_changed_at = now
        source.reason_codes = ["merged_into_another_event"]
        source.explanation = "该事件已由操作员合并到另一个事件, 历史记录继续保留。"
        source.updated_at = now
        source.version += 1
        alerts = _alerts_by_id(session, tuple(member.alert_id for member in members))
        for member in members:
            alert = alerts[member.alert_id]
            _add_decision(
                session,
                decision_id=self._id_factory("amd"),
                alert=alert,
                state="REMOVED",
                candidate_group_ids=(source.id,),
                selected_group_id=source.id,
                selected_group_version=before_source_version,
                reason="manually_removed_membership",
                explanation=command.reason,
                now=now,
            )
            _add_decision(
                session,
                decision_id=self._id_factory("amd"),
                alert=alert,
                state="MANUAL_CONFIRMED",
                candidate_group_ids=(target.id,),
                selected_group_id=target.id,
                selected_group_version=target.version,
                reason="manually_merged_membership",
                explanation=command.reason,
                now=now,
            )
        target.profile_version += 1
        save_current_event_profile(repository, target, now=now)
        schedule_event_lifecycle(repository, target, now=now)
        repository.schedule_correlation(target, target_version=target.version, now=now)
        moved_ids = tuple(sorted(member.alert_id for member in members))
        _audit(
            session,
            self._id_factory("aud"),
            actor,
            "alert_event.events_merged",
            source.id,
            request_id,
            "manual_event_merge",
            moved_ids,
            now,
            target_group_id=target.id,
        )
        return _MutationResult(
            source_group_id=source.id,
            target_group_id=target.id,
            source_version=source.version,
            target_version=target.version,
            moved_alert_ids=moved_ids,
            before_versions={source.id: before_source_version, target.id: before_target_version},
            after_versions={source.id: source.version, target.id: target.version},
        )

    def _load_replay(
        self, scope: str, key_hash: str, fingerprint: str
    ) -> AlertEventOperationResult | None:
        with self._session_factory() as session:
            operation = _find_operation(session, scope, key_hash)
            if operation is None:
                return None
            return _replay(operation, fingerprint)


def _new_split_group(
    *,
    group_id: str,
    source: AlertGroupRow,
    representative_alert_id: str,
    reason: str,
    now: datetime,
) -> AlertGroupRow:
    return AlertGroupRow(
        id=group_id,
        state=source.state,
        storm_state="NORMAL",
        rule_version=GROUPING_RULE_VERSION,
        service=source.service,
        entity_type=source.entity_type,
        entity_key=source.entity_key,
        entity_display_name=source.entity_display_name,
        problem_key=source.problem_key,
        problem_type=source.problem_type,
        scope_type=source.scope_type,
        scope_key=source.scope_key,
        scope_display_name=source.scope_display_name,
        signature_version=source.signature_version,
        environment=source.environment,
        symptom=source.symptom,
        title=source.title,
        severity=source.severity,
        representative_alert_id=representative_alert_id,
        incident_id=source.incident_id,
        first_observed_at=source.first_observed_at,
        last_observed_at=source.last_observed_at,
        state_changed_at=now,
        last_member_at=now,
        active_count=1,
        total_count=1,
        impacted_resource_count=1,
        desired_correlation_version=0,
        profile_version=1,
        forming_until=source.forming_until,
        observing_until=source.observing_until,
        closed_at=source.closed_at,
        member_limit=source.member_limit,
        continuation_group_id=None,
        pending_count=0,
        reason_codes=["created_by_manual_split"],
        explanation=reason,
        created_at=now,
        updated_at=now,
        version=1,
    )


def _find_pending_decision(
    session: Session,
    alert: AlertRow,
    group_id: str,
) -> AlertEventMembershipDecisionRow | None:
    rows = session.scalars(
        select(AlertEventMembershipDecisionRow)
        .where(
            AlertEventMembershipDecisionRow.alert_id == alert.id,
            AlertEventMembershipDecisionRow.alert_cycle == alert.cycle,
            AlertEventMembershipDecisionRow.state == "PENDING",
        )
        .order_by(
            AlertEventMembershipDecisionRow.created_at.desc(),
            AlertEventMembershipDecisionRow.id.desc(),
        )
        .limit(20)
        .with_for_update()
    )
    return next((row for row in rows if group_id in row.candidate_group_ids), None)


def _alerts_by_id(session: Session, alert_ids: tuple[str, ...]) -> dict[str, AlertRow]:
    rows = tuple(
        session.scalars(
            select(AlertRow)
            .where(AlertRow.id.in_(alert_ids))
            .order_by(AlertRow.id)
            .with_for_update()
        )
    )
    if len(rows) != len(alert_ids):
        raise AlertEventMemberConflict("alert_event_alert_not_found")
    return {row.id: row for row in rows}


def _add_decision(
    session: Session,
    *,
    decision_id: str,
    alert: AlertRow,
    state: str,
    candidate_group_ids: tuple[str, ...],
    selected_group_id: str,
    selected_group_version: int,
    reason: str,
    explanation: str,
    now: datetime,
) -> None:
    session.add(
        AlertEventMembershipDecisionRow(
            id=decision_id,
            alert_id=alert.id,
            alert_cycle=alert.cycle,
            alert_version=alert.version,
            state=state,
            candidate_group_ids=list(candidate_group_ids),
            selected_group_id=selected_group_id,
            selected_group_version=selected_group_version,
            rule_version=GROUPING_RULE_VERSION,
            scores={},
            reason_codes=[reason],
            explanation=explanation,
            created_at=now,
        )
    )


def _audit(
    session: Session,
    audit_id: str,
    actor: str,
    action: str,
    resource_id: str,
    request_id: str,
    reason_code: str,
    alert_ids: tuple[str, ...],
    now: datetime,
    *,
    target_group_id: str | None = None,
) -> None:
    details = {
        "reason_code": reason_code,
        "alert_ids": ",".join(alert_ids),
        "target_group_id": target_group_id or "",
    }
    RecordRepositories(session).add_audit(
        audit_id=audit_id,
        actor=actor,
        action=action,
        resource_type="alert_group",
        resource_id=resource_id,
        request_id=request_id,
        details=details,
        created_at=now,
    )


def _find_operation(session: Session, scope: str, key_hash: str) -> AlertEventOperationRow | None:
    return session.scalar(
        select(AlertEventOperationRow).where(
            AlertEventOperationRow.scope == scope,
            AlertEventOperationRow.idempotency_key_hash == key_hash,
        )
    )


def _fingerprint(kind: OperationKind, actor: str, payload: dict[str, object]) -> str:
    encoded = json.dumps(
        {"actor": actor, "kind": kind, "payload": payload},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(encoded.encode()).hexdigest()


def _replay(
    operation: AlertEventOperationRow,
    fingerprint: str,
) -> AlertEventOperationResult:
    if operation.command_fingerprint != fingerprint:
        raise AlertEventOperationConflict()
    return AlertEventOperationResult.model_validate(operation.result)


def _translate_rule(error: AlertEventOperationRuleError) -> AlertEventOperationError:
    if error.reason_code == "alert_event_environment_conflict":
        return AlertEventEnvironmentConflict()
    if error.reason_code == "alert_event_incident_conflict":
        return AlertEventIncidentConflict()
    if error.reason_code == "alert_event_member_limit_exceeded":
        return AlertEventCapacityConflict()
    return AlertEventMemberConflict(error.reason_code)


def _validate_metadata(actor: str, idempotency_key: str, request_id: str) -> None:
    if not 1 <= len(actor) <= 128:
        raise AlertEventMemberConflict("invalid_alert_event_actor")
    if not 1 <= len(idempotency_key) <= 256:
        raise AlertEventMemberConflict("invalid_alert_event_idempotency_key")
    if not 1 <= len(request_id) <= 64:
        raise AlertEventMemberConflict("invalid_alert_event_request_id")
