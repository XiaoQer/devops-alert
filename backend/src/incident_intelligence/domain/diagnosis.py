from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from incident_intelligence.domain.models import (
    AlertName,
    Environment,
    ServiceName,
    Severity,
    UtcAwareDatetime,
)

DiagnosisRunState = Literal[
    "QUEUED",
    "RUNNING",
    "REPORT_READY",
    "REVIEW_REQUIRED",
    "FAILED",
]
DiagnosisReferenceKind = Literal["EVIDENCE", "KNOWLEDGE"]
ReferenceId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]
ReportText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000),
]


class _FrozenDiagnosisModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DiagnosisReference(_FrozenDiagnosisModel):
    kind: DiagnosisReferenceKind
    target_id: ReferenceId
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    alias: Annotated[str, StringConstraints(pattern=r"^[EK][1-9][0-9]{0,2}$")] | None = None


class DiagnosisAlertFact(_FrozenDiagnosisModel):
    id: str = Field(pattern=r"^alt_[0-9a-f]{32}$")
    alert_name: AlertName
    state: Literal["ACTIVE", "RESOLVED"]
    severity: Severity
    first_received_at: UtcAwareDatetime


class DiagnosisSnapshot(_FrozenDiagnosisModel):
    diagnosis_run_id: str = Field(pattern=r"^drun_[0-9a-f]{32}$")
    incident_id: str = Field(pattern=r"^inc_[0-9a-f]{32}$")
    evidence_run_id: str = Field(pattern=r"^evr_[0-9a-f]{32}$")
    environment: Environment
    service_name: ServiceName | None
    alert_names: tuple[AlertName, ...] = Field(max_length=200)
    alert_facts: tuple[DiagnosisAlertFact, ...] = Field(default=(), max_length=500)
    evidence_references: tuple[DiagnosisReference, ...] = Field(max_length=100)
    knowledge_references: tuple[DiagnosisReference, ...] = Field(default=(), max_length=100)
    created_at: UtcAwareDatetime

    def allowed_references(self) -> dict[str, DiagnosisReference]:
        return {
            reference.target_id: reference
            for reference in (*self.evidence_references, *self.knowledge_references)
        }


class DiagnosisRun(_FrozenDiagnosisModel):
    id: str = Field(pattern=r"^drun_[0-9a-f]{32}$")
    incident_id: str = Field(pattern=r"^inc_[0-9a-f]{32}$")
    evidence_run_id: str = Field(pattern=r"^evr_[0-9a-f]{32}$")
    state: DiagnosisRunState
    requested_by: str = Field(min_length=1, max_length=128)
    created_at: UtcAwareDatetime
    started_at: UtcAwareDatetime | None = None
    completed_at: UtcAwareDatetime | None = None
    version: int = Field(default=1, ge=1)


class ConfirmedFact(_FrozenDiagnosisModel):
    text: ReportText
    reference_ids: tuple[ReferenceId, ...] = Field(max_length=5)


class Hypothesis(_FrozenDiagnosisModel):
    text: ReportText
    verification_required: Literal[True]
    reference_ids: tuple[ReferenceId, ...] = Field(min_length=1, max_length=5)


class DiagnosisReport(_FrozenDiagnosisModel):
    confirmed_facts: tuple[ConfirmedFact, ...] = Field(max_length=10)
    hypotheses: tuple[Hypothesis, ...] = Field(max_length=10)
    references: tuple[DiagnosisReference, ...] = Field(max_length=20)
    unknowns: tuple[ReportText, ...] = Field(max_length=10)
    suggested_human_actions: tuple[ReportText, ...] = Field(max_length=10)


class ReportValidationResult(_FrozenDiagnosisModel):
    accepted: bool
    reason_code: str | None = None
    report: DiagnosisReport | None = None


def transition_diagnosis_run(
    run: DiagnosisRun,
    *,
    state: DiagnosisRunState,
    now: UtcAwareDatetime,
) -> DiagnosisRun:
    allowed = {
        ("QUEUED", "RUNNING"),
        ("RUNNING", "REPORT_READY"),
        ("RUNNING", "REVIEW_REQUIRED"),
        ("RUNNING", "FAILED"),
    }
    if (run.state, state) not in allowed:
        raise ValueError("diagnosis_transition_invalid")
    return run.model_copy(
        update={
            "state": state,
            "started_at": now if state == "RUNNING" else run.started_at,
            "completed_at": now if state in {"REPORT_READY", "REVIEW_REQUIRED", "FAILED"} else None,
            "version": run.version + 1,
        }
    )


def validate_candidate_report(
    snapshot: DiagnosisSnapshot,
    candidate: object,
) -> ReportValidationResult:
    try:
        report = DiagnosisReport.model_validate(_expand_reference_aliases(snapshot, candidate))
    except ValueError:
        return ReportValidationResult(accepted=False, reason_code="diagnosis_report_invalid")

    allowed = snapshot.allowed_references()
    declared = {reference.target_id: reference for reference in report.references}
    if any(reference.target_id not in allowed for reference in report.references):
        return ReportValidationResult(accepted=False, reason_code="diagnosis_reference_not_allowed")
    if any(
        not _same_reference(declared.get(target_id), allowed.get(target_id))
        for target_id in _referenced_ids(report)
    ):
        return ReportValidationResult(accepted=False, reason_code="diagnosis_reference_not_allowed")
    if any(not fact.reference_ids for fact in report.confirmed_facts):
        return ReportValidationResult(
            accepted=False,
            reason_code="diagnosis_fact_reference_missing",
        )
    if any(
        allowed[reference_id].kind != "EVIDENCE"
        for fact in report.confirmed_facts
        for reference_id in fact.reference_ids
    ):
        return ReportValidationResult(
            accepted=False,
            reason_code="diagnosis_fact_reference_invalid",
        )
    if any(_contains_executable_action(action) for action in report.suggested_human_actions):
        return ReportValidationResult(accepted=False, reason_code="diagnosis_action_not_human_only")
    return ReportValidationResult(accepted=True, report=report)


def _expand_reference_aliases(snapshot: DiagnosisSnapshot, candidate: object) -> object:
    """Resolve model-facing aliases into immutable snapshot identities before validation."""
    if not isinstance(candidate, dict):
        return candidate
    aliases = {
        reference.alias: reference
        for reference in (*snapshot.evidence_references, *snapshot.knowledge_references)
        if reference.alias is not None
    }

    def resolve(value: object) -> object:
        reference = aliases.get(value) if isinstance(value, str) else None
        return reference.target_id if reference is not None else value

    expanded = dict(candidate)
    for key in ("confirmed_facts", "hypotheses"):
        entries = expanded.get(key)
        if isinstance(entries, list):
            expanded[key] = [
                {
                    **entry,
                    "reference_ids": [resolve(item) for item in entry.get("reference_ids", [])],
                }
                if isinstance(entry, dict)
                else entry
                for entry in entries
            ]
    references = expanded.get("references")
    if isinstance(references, list):
        expanded["references"] = [
            {
                "kind": reference.kind,
                "target_id": reference.target_id,
                "content_hash": reference.content_hash,
            }
            if isinstance(item, str) and (reference := aliases.get(item)) is not None
            else item
            for item in references
        ]
    return expanded


def _referenced_ids(report: DiagnosisReport) -> tuple[str, ...]:
    confirmed_ids = tuple(
        reference_id for fact in report.confirmed_facts for reference_id in fact.reference_ids
    )
    hypothesis_ids = tuple(
        reference_id
        for hypothesis in report.hypotheses
        for reference_id in hypothesis.reference_ids
    )
    return confirmed_ids + hypothesis_ids


def _same_reference(
    declared: DiagnosisReference | None,
    allowed: DiagnosisReference | None,
) -> bool:
    return (
        declared is not None
        and allowed is not None
        and (
            declared.kind,
            declared.target_id,
            declared.content_hash,
        )
        == (allowed.kind, allowed.target_id, allowed.content_hash)
    )


def _contains_executable_action(action: str) -> bool:
    return bool(
        re.search(
            r"```|\$\s|(?:^|[;\n]|\b(?:执行|运行|run)\s+)\s*"
            r"(?:kubectl|curl|wget|ssh|mysql|psql|terraform|ansible|rm|chmod)\b",
            action,
            re.IGNORECASE,
        )
    )
