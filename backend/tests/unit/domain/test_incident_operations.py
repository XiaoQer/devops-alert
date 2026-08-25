from __future__ import annotations

import pytest

from incident_intelligence.domain.enums import IncidentState
from incident_intelligence.domain.incident_operations import (
    IncidentActivityKind,
    IncidentNoteCategory,
    IncidentOperationKind,
    IncidentResolutionCategory,
    allowed_actions,
    allowed_transitions,
    primary_action,
)


@pytest.mark.parametrize(
    ("current", "targets"),
    [
        (IncidentState.DETECTED, (IncidentState.TRIAGING, IncidentState.INVESTIGATING)),
        (IncidentState.TRIAGING, (IncidentState.INVESTIGATING, IncidentState.MITIGATING)),
        (
            IncidentState.INVESTIGATING,
            (IncidentState.MITIGATING, IncidentState.MONITORING_RECOVERY),
        ),
        (
            IncidentState.MITIGATING,
            (IncidentState.INVESTIGATING, IncidentState.MONITORING_RECOVERY),
        ),
        (
            IncidentState.MONITORING_RECOVERY,
            (IncidentState.INVESTIGATING, IncidentState.MITIGATING),
        ),
        (IncidentState.RESOLVED, ()),
        (IncidentState.CLOSED, ()),
    ],
)
def test_normal_transition_choices_match_the_approved_state_machine(
    current: IncidentState, targets: tuple[IncidentState, ...]
) -> None:
    assert allowed_transitions(current) == targets


def test_active_incident_actions_depend_on_current_assignee() -> None:
    unclaimed = allowed_actions(IncidentState.INVESTIGATING, None, "manual-api-client")
    mine = allowed_actions(IncidentState.INVESTIGATING, "manual-api-client", "manual-api-client")
    another = allowed_actions(IncidentState.INVESTIGATING, "another-operator", "manual-api-client")

    assert unclaimed == (
        IncidentOperationKind.CLAIM,
        IncidentOperationKind.TRANSITION,
        IncidentOperationKind.ADD_NOTE,
        IncidentOperationKind.RESOLVE,
    )
    assert mine == (
        IncidentOperationKind.RELEASE,
        IncidentOperationKind.TRANSITION,
        IncidentOperationKind.ADD_NOTE,
        IncidentOperationKind.RESOLVE,
    )
    assert another == (
        IncidentOperationKind.TRANSITION,
        IncidentOperationKind.ADD_NOTE,
        IncidentOperationKind.RESOLVE,
    )


def test_resolved_and_closed_actions_are_explicit() -> None:
    assert allowed_actions(IncidentState.RESOLVED, None, "manual-api-client") == (
        IncidentOperationKind.REOPEN,
        IncidentOperationKind.CLOSE,
    )
    assert allowed_actions(IncidentState.CLOSED, None, "manual-api-client") == ()


@pytest.mark.parametrize(
    ("state", "action", "target"),
    [
        (IncidentState.DETECTED, IncidentOperationKind.TRANSITION, IncidentState.TRIAGING),
        (
            IncidentState.TRIAGING,
            IncidentOperationKind.TRANSITION,
            IncidentState.INVESTIGATING,
        ),
        (
            IncidentState.INVESTIGATING,
            IncidentOperationKind.TRANSITION,
            IncidentState.MITIGATING,
        ),
        (
            IncidentState.MITIGATING,
            IncidentOperationKind.TRANSITION,
            IncidentState.MONITORING_RECOVERY,
        ),
        (IncidentState.MONITORING_RECOVERY, IncidentOperationKind.RESOLVE, None),
        (IncidentState.RESOLVED, IncidentOperationKind.CLOSE, None),
    ],
)
def test_primary_action_is_a_deterministic_workflow_default(
    state: IncidentState,
    action: IncidentOperationKind,
    target: IncidentState | None,
) -> None:
    result = primary_action(state)
    assert result is not None
    assert result.action is action
    assert result.target_state is target


def test_closed_incident_has_no_primary_action() -> None:
    assert primary_action(IncidentState.CLOSED) is None


def test_operation_enums_are_fixed_and_bounded() -> None:
    assert tuple(IncidentActivityKind) == (
        IncidentActivityKind.INCIDENT_CLAIMED,
        IncidentActivityKind.INCIDENT_RELEASED,
        IncidentActivityKind.STATE_TRANSITIONED,
        IncidentActivityKind.NOTE_ADDED,
        IncidentActivityKind.INCIDENT_RESOLVED,
        IncidentActivityKind.INCIDENT_REOPENED,
        IncidentActivityKind.INCIDENT_CLOSED,
    )
    assert tuple(IncidentNoteCategory) == (
        IncidentNoteCategory.CURRENT_FINDING,
        IncidentNoteCategory.ACTION_TAKEN,
        IncidentNoteCategory.ACTION_RESULT,
        IncidentNoteCategory.NEXT_STEP,
        IncidentNoteCategory.GENERAL,
    )
    assert tuple(IncidentResolutionCategory) == (
        IncidentResolutionCategory.RECOVERED,
        IncidentResolutionCategory.FALSE_POSITIVE,
        IncidentResolutionCategory.DUPLICATE,
        IncidentResolutionCategory.NO_ACTION,
        IncidentResolutionCategory.OTHER,
    )
