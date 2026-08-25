from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from incident_intelligence.domain.enums import IncidentState
from incident_intelligence.domain.transitions import INCIDENT_TRANSITION_CHOICES


class IncidentActivityKind(StrEnum):
    INCIDENT_CLAIMED = "INCIDENT_CLAIMED"
    INCIDENT_RELEASED = "INCIDENT_RELEASED"
    STATE_TRANSITIONED = "STATE_TRANSITIONED"
    NOTE_ADDED = "NOTE_ADDED"
    INCIDENT_RESOLVED = "INCIDENT_RESOLVED"
    INCIDENT_REOPENED = "INCIDENT_REOPENED"
    INCIDENT_CLOSED = "INCIDENT_CLOSED"


class IncidentOperationKind(StrEnum):
    CLAIM = "CLAIM"
    RELEASE = "RELEASE"
    TRANSITION = "TRANSITION"
    ADD_NOTE = "ADD_NOTE"
    RESOLVE = "RESOLVE"
    REOPEN = "REOPEN"
    CLOSE = "CLOSE"


class IncidentNoteCategory(StrEnum):
    CURRENT_FINDING = "CURRENT_FINDING"
    ACTION_TAKEN = "ACTION_TAKEN"
    ACTION_RESULT = "ACTION_RESULT"
    NEXT_STEP = "NEXT_STEP"
    GENERAL = "GENERAL"


class IncidentResolutionCategory(StrEnum):
    RECOVERED = "RECOVERED"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    DUPLICATE = "DUPLICATE"
    NO_ACTION = "NO_ACTION"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class IncidentPrimaryAction:
    action: IncidentOperationKind
    target_state: IncidentState | None


PRIMARY_ACTIONS: dict[IncidentState, IncidentPrimaryAction] = {
    IncidentState.DETECTED: IncidentPrimaryAction(
        IncidentOperationKind.TRANSITION, IncidentState.TRIAGING
    ),
    IncidentState.TRIAGING: IncidentPrimaryAction(
        IncidentOperationKind.TRANSITION, IncidentState.INVESTIGATING
    ),
    IncidentState.INVESTIGATING: IncidentPrimaryAction(
        IncidentOperationKind.TRANSITION, IncidentState.MITIGATING
    ),
    IncidentState.MITIGATING: IncidentPrimaryAction(
        IncidentOperationKind.TRANSITION, IncidentState.MONITORING_RECOVERY
    ),
    IncidentState.MONITORING_RECOVERY: IncidentPrimaryAction(IncidentOperationKind.RESOLVE, None),
    IncidentState.RESOLVED: IncidentPrimaryAction(IncidentOperationKind.CLOSE, None),
}


def allowed_transitions(state: IncidentState) -> tuple[IncidentState, ...]:
    return INCIDENT_TRANSITION_CHOICES.get(state, ())


def allowed_actions(
    state: IncidentState, assignee: str | None, actor: str
) -> tuple[IncidentOperationKind, ...]:
    if state is IncidentState.CLOSED:
        return ()
    if state is IncidentState.RESOLVED:
        return (IncidentOperationKind.REOPEN, IncidentOperationKind.CLOSE)

    actions: list[IncidentOperationKind] = []
    if assignee is None:
        actions.append(IncidentOperationKind.CLAIM)
    elif assignee == actor:
        actions.append(IncidentOperationKind.RELEASE)
    if allowed_transitions(state):
        actions.append(IncidentOperationKind.TRANSITION)
    actions.extend((IncidentOperationKind.ADD_NOTE, IncidentOperationKind.RESOLVE))
    return tuple(actions)


def primary_action(state: IncidentState) -> IncidentPrimaryAction | None:
    return PRIMARY_ACTIONS.get(state)
