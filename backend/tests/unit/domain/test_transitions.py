from __future__ import annotations

import pytest

from incident_intelligence.domain.enums import AlertState, DiagnosisState, IncidentState
from incident_intelligence.domain.transitions import (
    InvalidStateTransition,
    require_alert_transition,
    require_diagnosis_transition,
    require_incident_transition,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (AlertState.ACTIVE, AlertState.RESOLVED),
        (AlertState.ACTIVE, AlertState.SUPPRESSED),
    ],
)
def test_alert_allows_only_declared_forward_transitions(
    current: AlertState, target: AlertState
) -> None:
    require_alert_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (IncidentState.DETECTED, IncidentState.TRIAGING),
        (IncidentState.DETECTED, IncidentState.INVESTIGATING),
        (IncidentState.TRIAGING, IncidentState.INVESTIGATING),
        (IncidentState.TRIAGING, IncidentState.MITIGATING),
        (IncidentState.INVESTIGATING, IncidentState.MITIGATING),
        (IncidentState.INVESTIGATING, IncidentState.MONITORING_RECOVERY),
        (IncidentState.MITIGATING, IncidentState.INVESTIGATING),
        (IncidentState.MITIGATING, IncidentState.MONITORING_RECOVERY),
        (IncidentState.MONITORING_RECOVERY, IncidentState.INVESTIGATING),
        (IncidentState.MONITORING_RECOVERY, IncidentState.MITIGATING),
    ],
)
def test_incident_allows_each_declared_forward_transition(
    current: IncidentState, target: IncidentState
) -> None:
    require_incident_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (DiagnosisState.QUEUED, DiagnosisState.COLLECTING),
        (DiagnosisState.COLLECTING, DiagnosisState.NORMALIZING),
        (DiagnosisState.NORMALIZING, DiagnosisState.SNAPSHOT_READY),
        (DiagnosisState.SNAPSHOT_READY, DiagnosisState.ANALYZING),
        (DiagnosisState.ANALYZING, DiagnosisState.REPORT_READY),
        (DiagnosisState.ANALYZING, DiagnosisState.REVIEW_REQUIRED),
        (DiagnosisState.COLLECTING, DiagnosisState.PARTIAL),
        (DiagnosisState.NORMALIZING, DiagnosisState.FAILED),
    ],
)
def test_diagnosis_allows_declared_success_and_failure_branches(
    current: DiagnosisState, target: DiagnosisState
) -> None:
    require_diagnosis_transition(current, target)


@pytest.mark.parametrize(
    ("validator", "current", "target", "domain"),
    [
        (require_alert_transition, AlertState.RESOLVED, AlertState.ACTIVE, "alert"),
        (
            require_incident_transition,
            IncidentState.MONITORING_RECOVERY,
            IncidentState.RESOLVED,
            "incident",
        ),
        (
            require_diagnosis_transition,
            DiagnosisState.REPORT_READY,
            DiagnosisState.ANALYZING,
            "diagnosis",
        ),
    ],
)
def test_state_policies_reject_backward_skip_and_terminal_transitions(
    validator: object, current: object, target: object, domain: str
) -> None:
    with pytest.raises(InvalidStateTransition) as error:
        validator(current, target)  # type: ignore[operator]

    assert error.value.domain == domain
    assert error.value.current == str(current)
    assert error.value.target == str(target)
