from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.enums import IncidentState
from incident_intelligence.domain.incident_operations import (
    IncidentActivityKind,
    IncidentNoteCategory,
    IncidentOperationKind,
    IncidentResolutionCategory,
)
from incident_intelligence.domain.transitions import (
    InvalidStateTransition,
    require_incident_transition,
)
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.incident_operation_repository import (
    IncidentOperationRepository,
)
from incident_intelligence.persistence.models import (
    IncidentActivityRow,
    IncidentOperationRow,
    IncidentRow,
)
from incident_intelligence.persistence.repositories import RecordRepositories
from incident_intelligence.services.incident_center import IncidentResourceNotFound


class IncidentOperationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    action: IncidentOperationKind
    state: IncidentState
    assignee: str | None
    version: int
    activity_id: str
    occurred_at: datetime


class IncidentOperationError(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code

    def __str__(self) -> str:
        return self.reason_code


class IncidentVersionConflict(IncidentOperationError):
    def __init__(self) -> None:
        super().__init__("incident_version_conflict")


class IncidentOperationConflict(IncidentOperationError):
    def __init__(self) -> None:
        super().__init__("incident_operation_conflict")


class IncidentAlreadyClaimed(IncidentOperationError):
    def __init__(self) -> None:
        super().__init__("incident_already_claimed")


class IncidentNotClaimed(IncidentOperationError):
    def __init__(self) -> None:
        super().__init__("incident_not_claimed")


class IncidentClosed(IncidentOperationError):
    def __init__(self) -> None:
        super().__init__("incident_closed")


class InvalidIncidentOperation(IncidentOperationError):
    def __init__(self, reason_code: str = "invalid_incident_operation") -> None:
        super().__init__(reason_code)


class InvalidIncidentTransition(IncidentOperationError):
    def __init__(self) -> None:
        super().__init__("invalid_incident_transition")


@dataclass(frozen=True, slots=True)
class _Command:
    incident_id: str
    action: IncidentOperationKind
    expected_version: int
    actor: str
    idempotency_key: str
    request_id: str
    target_state: IncidentState | None = None
    note_category: IncidentNoteCategory | None = None
    resolution_category: IncidentResolutionCategory | None = None
    message: str | None = None
    resolution_actions: str | None = None
    root_cause: str | None = None

    @property
    def scope(self) -> str:
        return f"incident.operation.{self.action.value.lower()}"


class IncidentOperationService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[IdPrefix], str] = new_id,
        before_lock: Callable[[], object] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory
        self._before_lock = before_lock

    def claim(
        self,
        incident_id: str,
        *,
        expected_version: int,
        actor: str,
        idempotency_key: str,
        request_id: str,
    ) -> IncidentOperationResult:
        return self._execute(
            _Command(
                incident_id=incident_id,
                action=IncidentOperationKind.CLAIM,
                expected_version=expected_version,
                actor=actor,
                idempotency_key=idempotency_key,
                request_id=request_id,
            )
        )

    def release(
        self,
        incident_id: str,
        *,
        expected_version: int,
        actor: str,
        idempotency_key: str,
        request_id: str,
    ) -> IncidentOperationResult:
        return self._execute(
            _Command(
                incident_id=incident_id,
                action=IncidentOperationKind.RELEASE,
                expected_version=expected_version,
                actor=actor,
                idempotency_key=idempotency_key,
                request_id=request_id,
            )
        )

    def transition(
        self,
        incident_id: str,
        *,
        expected_version: int,
        target_state: IncidentState,
        message: str,
        actor: str,
        idempotency_key: str,
        request_id: str,
    ) -> IncidentOperationResult:
        return self._execute(
            _Command(
                incident_id=incident_id,
                action=IncidentOperationKind.TRANSITION,
                expected_version=expected_version,
                target_state=target_state,
                message=_required_text(message, maximum=1_000),
                actor=actor,
                idempotency_key=idempotency_key,
                request_id=request_id,
            )
        )

    def add_note(
        self,
        incident_id: str,
        *,
        expected_version: int,
        category: IncidentNoteCategory,
        message: str,
        actor: str,
        idempotency_key: str,
        request_id: str,
    ) -> IncidentOperationResult:
        return self._execute(
            _Command(
                incident_id=incident_id,
                action=IncidentOperationKind.ADD_NOTE,
                expected_version=expected_version,
                note_category=category,
                message=_required_text(message, maximum=2_000),
                actor=actor,
                idempotency_key=idempotency_key,
                request_id=request_id,
            )
        )

    def resolve(
        self,
        incident_id: str,
        *,
        expected_version: int,
        category: IncidentResolutionCategory,
        message: str,
        resolution_actions: str,
        root_cause: str | None,
        actor: str,
        idempotency_key: str,
        request_id: str,
    ) -> IncidentOperationResult:
        return self._execute(
            _Command(
                incident_id=incident_id,
                action=IncidentOperationKind.RESOLVE,
                expected_version=expected_version,
                resolution_category=category,
                message=_required_text(message, maximum=2_000),
                resolution_actions=_required_text(resolution_actions, maximum=4_000),
                root_cause=_optional_text(root_cause, maximum=4_000),
                actor=actor,
                idempotency_key=idempotency_key,
                request_id=request_id,
            )
        )

    def reopen(
        self,
        incident_id: str,
        *,
        expected_version: int,
        reason: str,
        actor: str,
        idempotency_key: str,
        request_id: str,
    ) -> IncidentOperationResult:
        return self._execute(
            _Command(
                incident_id=incident_id,
                action=IncidentOperationKind.REOPEN,
                expected_version=expected_version,
                message=_required_text(reason, maximum=2_000),
                actor=actor,
                idempotency_key=idempotency_key,
                request_id=request_id,
            )
        )

    def close(
        self,
        incident_id: str,
        *,
        expected_version: int,
        message: str,
        actor: str,
        idempotency_key: str,
        request_id: str,
    ) -> IncidentOperationResult:
        return self._execute(
            _Command(
                incident_id=incident_id,
                action=IncidentOperationKind.CLOSE,
                expected_version=expected_version,
                message=_required_text(message, maximum=2_000),
                actor=actor,
                idempotency_key=idempotency_key,
                request_id=request_id,
            )
        )

    def _execute(self, command: _Command) -> IncidentOperationResult:
        _validate_metadata(command)
        key_hash = sha256(command.idempotency_key.encode()).hexdigest()
        fingerprint = _command_fingerprint(command)
        try:
            return self._execute_once(command, key_hash, fingerprint)
        except IntegrityError:
            replay = self._load_replay(command.scope, key_hash, fingerprint)
            if replay is None:
                raise
            return replay

    def _execute_once(
        self, command: _Command, key_hash: str, fingerprint: str
    ) -> IncidentOperationResult:
        with self._session_factory.begin() as session:
            repository = IncidentOperationRepository(session)
            existing = repository.find_operation(command.scope, key_hash)
            if existing is not None:
                return _replay(existing, fingerprint)
            if self._before_lock is not None:
                self._before_lock()
            incident = repository.lock_incident(command.incident_id)
            if incident is None:
                raise IncidentResourceNotFound()
            existing = repository.find_operation(command.scope, key_hash)
            if existing is not None:
                return _replay(existing, fingerprint)
            if incident.version != command.expected_version:
                raise IncidentVersionConflict()

            now = self._clock().astimezone(UTC)
            before_state = IncidentState(incident.state)
            activity_kind = _apply(command, incident, now)
            incident.version += 1
            activity = IncidentActivityRow(
                id=self._id_factory("iact"),
                incident_id=incident.id,
                kind=activity_kind.value,
                actor=command.actor,
                from_state=before_state.value
                if before_state != IncidentState(incident.state)
                else None,
                to_state=incident.state if before_state != IncidentState(incident.state) else None,
                note_category=(
                    None if command.note_category is None else command.note_category.value
                ),
                message=command.message,
                resolution_category=(
                    None
                    if command.resolution_category is None
                    else command.resolution_category.value
                ),
                resolution_actions=command.resolution_actions,
                root_cause=command.root_cause,
                incident_version=incident.version,
                created_at=now,
            )
            repository.add_activity(activity)
            RecordRepositories(session).add_audit(
                audit_id=self._id_factory("aud"),
                actor=command.actor,
                action=_AUDIT_ACTIONS[command.action],
                resource_type="incident",
                resource_id=incident.id,
                request_id=command.request_id,
                details={
                    "reason_code": _AUDIT_REASONS[command.action],
                    "activity_id": activity.id,
                },
                created_at=now,
            )
            operation = IncidentOperationRow(
                id=self._id_factory("iop"),
                scope=command.scope,
                idempotency_key_hash=key_hash,
                command_fingerprint=fingerprint,
                incident_id=incident.id,
                action=command.action.value,
                result_state=incident.state,
                result_assignee=incident.assignee,
                result_version=incident.version,
                activity_id=activity.id,
                completed_at=now,
            )
            repository.add_operation(operation)
            repository.flush()
            return _result(operation)

    def _load_replay(
        self, scope: str, key_hash: str, fingerprint: str
    ) -> IncidentOperationResult | None:
        with self._session_factory() as session:
            operation = IncidentOperationRepository(session).find_operation(scope, key_hash)
            if operation is None:
                return None
            return _replay(operation, fingerprint)


def _apply(command: _Command, incident: IncidentRow, now: datetime) -> IncidentActivityKind:
    state = IncidentState(incident.state)
    if state is IncidentState.CLOSED:
        raise IncidentClosed()
    if command.action is IncidentOperationKind.CLAIM:
        if state is IncidentState.RESOLVED:
            raise InvalidIncidentOperation()
        if incident.assignee is not None:
            raise IncidentAlreadyClaimed()
        incident.assignee = command.actor
        incident.claimed_at = now
        return IncidentActivityKind.INCIDENT_CLAIMED
    if command.action is IncidentOperationKind.RELEASE:
        if state is IncidentState.RESOLVED:
            raise InvalidIncidentOperation()
        if incident.assignee is None or incident.assignee != command.actor:
            raise IncidentNotClaimed()
        incident.assignee = None
        incident.claimed_at = None
        return IncidentActivityKind.INCIDENT_RELEASED
    if command.action is IncidentOperationKind.TRANSITION:
        if command.target_state is None:
            raise InvalidIncidentOperation()
        try:
            require_incident_transition(state, command.target_state)
        except InvalidStateTransition as error:
            raise InvalidIncidentTransition() from error
        incident.state = command.target_state.value
        incident.state_changed_at = now
        return IncidentActivityKind.STATE_TRANSITIONED
    if command.action is IncidentOperationKind.ADD_NOTE:
        if state is IncidentState.RESOLVED:
            raise InvalidIncidentOperation()
        return IncidentActivityKind.NOTE_ADDED
    if command.action is IncidentOperationKind.RESOLVE:
        if state is IncidentState.RESOLVED:
            raise InvalidIncidentOperation()
        incident.state = IncidentState.RESOLVED.value
        incident.state_changed_at = now
        incident.resolved_at = now
        incident.closed_at = None
        return IncidentActivityKind.INCIDENT_RESOLVED
    if command.action is IncidentOperationKind.REOPEN:
        if state is not IncidentState.RESOLVED:
            raise InvalidIncidentOperation()
        incident.state = IncidentState.INVESTIGATING.value
        incident.state_changed_at = now
        incident.resolved_at = None
        incident.closed_at = None
        return IncidentActivityKind.INCIDENT_REOPENED
    if command.action is IncidentOperationKind.CLOSE:
        if state is not IncidentState.RESOLVED:
            raise InvalidIncidentOperation()
        incident.state = IncidentState.CLOSED.value
        incident.state_changed_at = now
        incident.closed_at = now
        return IncidentActivityKind.INCIDENT_CLOSED
    raise InvalidIncidentOperation()


def _command_fingerprint(command: _Command) -> str:
    value = {
        "action": command.action.value,
        "actor": command.actor,
        "expected_version": command.expected_version,
        "incident_id": command.incident_id,
        "message": command.message,
        "note_category": (None if command.note_category is None else command.note_category.value),
        "resolution_actions": command.resolution_actions,
        "resolution_category": (
            None if command.resolution_category is None else command.resolution_category.value
        ),
        "root_cause": command.root_cause,
        "target_state": None if command.target_state is None else command.target_state.value,
    }
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return sha256(encoded.encode()).hexdigest()


def _replay(operation: IncidentOperationRow, fingerprint: str) -> IncidentOperationResult:
    if operation.command_fingerprint != fingerprint:
        raise IncidentOperationConflict()
    return _result(operation)


def _result(operation: IncidentOperationRow) -> IncidentOperationResult:
    return IncidentOperationResult(
        id=operation.incident_id,
        action=IncidentOperationKind(operation.action),
        state=IncidentState(operation.result_state),
        assignee=operation.result_assignee,
        version=operation.result_version,
        activity_id=operation.activity_id,
        occurred_at=operation.completed_at,
    )


def _validate_metadata(command: _Command) -> None:
    if command.expected_version < 1:
        raise InvalidIncidentOperation()
    if not 1 <= len(command.actor) <= 128:
        raise InvalidIncidentOperation()
    if not 1 <= len(command.idempotency_key) <= 256:
        raise InvalidIncidentOperation()
    if not 1 <= len(command.request_id) <= 64:
        raise InvalidIncidentOperation()


def _required_text(value: str, *, maximum: int) -> str:
    normalized = value.strip()
    if not 1 <= len(normalized) <= maximum:
        raise InvalidIncidentOperation()
    return normalized


def _optional_text(value: str | None, *, maximum: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > maximum:
        raise InvalidIncidentOperation()
    return normalized


_AUDIT_ACTIONS = {
    IncidentOperationKind.CLAIM: "incident.claimed",
    IncidentOperationKind.RELEASE: "incident.released",
    IncidentOperationKind.TRANSITION: "incident.state_transitioned",
    IncidentOperationKind.ADD_NOTE: "incident.note_added",
    IncidentOperationKind.RESOLVE: "incident.resolved",
    IncidentOperationKind.REOPEN: "incident.reopened",
    IncidentOperationKind.CLOSE: "incident.closed",
}

_AUDIT_REASONS = {
    IncidentOperationKind.CLAIM: "manual_claim_requested",
    IncidentOperationKind.RELEASE: "manual_release_requested",
    IncidentOperationKind.TRANSITION: "manual_transition_requested",
    IncidentOperationKind.ADD_NOTE: "manual_note_requested",
    IncidentOperationKind.RESOLVE: "manual_resolve_requested",
    IncidentOperationKind.REOPEN: "manual_reopen_requested",
    IncidentOperationKind.CLOSE: "manual_close_requested",
}
