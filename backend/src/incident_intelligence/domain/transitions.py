from dataclasses import dataclass
from enum import StrEnum

from incident_intelligence.domain.enums import AlertState, DiagnosisState, IncidentState


@dataclass(frozen=True, slots=True)
class InvalidStateTransition(Exception):
    domain: str
    current: str
    target: str

    def __str__(self) -> str:
        return f"{self.domain} state cannot transition from {self.current} to {self.target}"


ALERT_TRANSITIONS: dict[AlertState, frozenset[AlertState]] = {
    AlertState.ACTIVE: frozenset({AlertState.RESOLVED, AlertState.SUPPRESSED}),
}

INCIDENT_TRANSITION_CHOICES: dict[IncidentState, tuple[IncidentState, ...]] = {
    IncidentState.DETECTED: (IncidentState.TRIAGING, IncidentState.INVESTIGATING),
    IncidentState.TRIAGING: (IncidentState.INVESTIGATING, IncidentState.MITIGATING),
    IncidentState.INVESTIGATING: (
        IncidentState.MITIGATING,
        IncidentState.MONITORING_RECOVERY,
    ),
    IncidentState.MITIGATING: (
        IncidentState.INVESTIGATING,
        IncidentState.MONITORING_RECOVERY,
    ),
    IncidentState.MONITORING_RECOVERY: (
        IncidentState.INVESTIGATING,
        IncidentState.MITIGATING,
    ),
    IncidentState.RESOLVED: (),
    IncidentState.CLOSED: (),
}
INCIDENT_TRANSITIONS: dict[IncidentState, frozenset[IncidentState]] = {
    state: frozenset(targets) for state, targets in INCIDENT_TRANSITION_CHOICES.items()
}

DIAGNOSIS_TRANSITIONS: dict[DiagnosisState, frozenset[DiagnosisState]] = {
    DiagnosisState.QUEUED: frozenset({DiagnosisState.COLLECTING, DiagnosisState.FAILED}),
    DiagnosisState.COLLECTING: frozenset(
        {DiagnosisState.NORMALIZING, DiagnosisState.PARTIAL, DiagnosisState.FAILED}
    ),
    DiagnosisState.NORMALIZING: frozenset(
        {DiagnosisState.SNAPSHOT_READY, DiagnosisState.PARTIAL, DiagnosisState.FAILED}
    ),
    DiagnosisState.SNAPSHOT_READY: frozenset(
        {DiagnosisState.ANALYZING, DiagnosisState.PARTIAL, DiagnosisState.FAILED}
    ),
    DiagnosisState.ANALYZING: frozenset(
        {
            DiagnosisState.REPORT_READY,
            DiagnosisState.REVIEW_REQUIRED,
            DiagnosisState.PARTIAL,
            DiagnosisState.FAILED,
        }
    ),
}


def _require_transition(
    domain: str,
    current: StrEnum,
    target: StrEnum,
    allowed: dict[StrEnum, frozenset[StrEnum]],
) -> None:
    if target not in allowed.get(current, frozenset()):
        raise InvalidStateTransition(domain, str(current), str(target))


def require_alert_transition(current: AlertState, target: AlertState) -> None:
    _require_transition("alert", current, target, ALERT_TRANSITIONS)  # type: ignore[arg-type]


def require_incident_transition(current: IncidentState, target: IncidentState) -> None:
    _require_transition("incident", current, target, INCIDENT_TRANSITIONS)  # type: ignore[arg-type]


def require_diagnosis_transition(current: DiagnosisState, target: DiagnosisState) -> None:
    _require_transition("diagnosis", current, target, DIAGNOSIS_TRANSITIONS)  # type: ignore[arg-type]
