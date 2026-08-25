# ruff: noqa: RUF001

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from incident_intelligence.domain.enums import (
    AlertState,
    CatalogState,
    CorrelationOutcome,
)
from incident_intelligence.domain.models import Environment, Severity

CorrelationAction = Literal["CREATE", "LINK", "NONE"]
CorrelationReasonCode = Literal[
    "alert_not_active",
    "severity_below_threshold",
    "non_production_environment",
    "service_not_registered",
    "service_inactive",
    "alert_version_superseded",
    "alert_already_linked",
    "alert_resolved_no_incident_close",
    "no_same_service_candidate",
    "one_exact_service_candidate",
    "multiple_exact_service_candidates",
    "dependency_symptom_candidate",
    "candidate_limit_reached",
]
IncidentId = Annotated[str, StringConstraints(pattern=r"^inc_[0-9a-f]{32}$")]
Explanation = Annotated[str, StringConstraints(min_length=1, max_length=500)]

EXPLANATIONS: dict[CorrelationReasonCode, str] = {
    "alert_not_active": "告警当前不是活动状态，未进入事故关联。",
    "severity_below_threshold": "告警严重度未达到 critical/high，未创建事故。",
    "non_production_environment": "告警不属于生产环境，未创建事故。",
    "service_not_registered": "服务尚未登记，未创建事故。",
    "service_inactive": "服务目录项已停用，未创建事故。",
    "alert_version_superseded": "该任务对应的告警版本已被新版本取代。",
    "alert_already_linked": "告警已经属于现有事故，保持原关联。",
    "alert_resolved_no_incident_close": "告警已经恢复，但事故不会自动关闭。",
    "no_same_service_candidate": "窗口内没有同服务事故，已创建独立事故。",
    "one_exact_service_candidate": "窗口内只有一个同服务事故，已自动关联。",
    "multiple_exact_service_candidates": "窗口内存在多个同服务事故，为避免误合并已创建独立事故。",
    "dependency_symptom_candidate": "发现一跳服务的同症状事故，仅标记可能相关。",
    "candidate_limit_reached": "候选数量超过安全上限，未执行自动合并。",
}


class CorrelationContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    alert_version: int = Field(ge=1)
    current_alert_version: int = Field(ge=1)
    alert_state: AlertState
    severity: Severity
    environment: Environment
    catalog_state: CatalogState | None
    existing_incident_id: IncidentId | None = None
    exact_candidate_ids: tuple[IncidentId, ...] = Field(default=(), max_length=21)
    dependency_candidate_ids: tuple[IncidentId, ...] = Field(default=(), max_length=21)


class CorrelationDecisionDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: CorrelationOutcome
    action: CorrelationAction
    selected_incident_id: IncidentId | None = None
    candidate_incident_ids: tuple[IncidentId, ...] = Field(default=(), max_length=20)
    reason_codes: tuple[CorrelationReasonCode, ...] = Field(
        min_length=1,
        max_length=10,
    )
    explanation: Explanation


def _decision(
    outcome: CorrelationOutcome,
    action: CorrelationAction,
    reason: CorrelationReasonCode,
    *,
    selected_incident_id: str | None = None,
    candidate_incident_ids: tuple[str, ...] = (),
) -> CorrelationDecisionDraft:
    return CorrelationDecisionDraft(
        outcome=outcome,
        action=action,
        selected_incident_id=selected_incident_id,
        candidate_incident_ids=candidate_incident_ids,
        reason_codes=(reason,),
        explanation=EXPLANATIONS[reason],
    )


def decide_correlation(context: CorrelationContext) -> CorrelationDecisionDraft:
    if context.alert_version != context.current_alert_version:
        return _decision(
            CorrelationOutcome.SUPERSEDED,
            "NONE",
            "alert_version_superseded",
        )

    if context.existing_incident_id is not None:
        if context.alert_state is AlertState.ACTIVE:
            return _decision(
                CorrelationOutcome.LINKED_EXISTING,
                "NONE",
                "alert_already_linked",
                selected_incident_id=context.existing_incident_id,
            )
        if context.alert_state is AlertState.RESOLVED:
            return _decision(
                CorrelationOutcome.RECORDED_RESOLUTION,
                "NONE",
                "alert_resolved_no_incident_close",
                selected_incident_id=context.existing_incident_id,
            )

    if context.alert_state is not AlertState.ACTIVE:
        return _decision(
            CorrelationOutcome.REJECTED_INELIGIBLE,
            "NONE",
            "alert_not_active",
        )
    if context.severity not in {"critical", "high"}:
        return _decision(
            CorrelationOutcome.REJECTED_INELIGIBLE,
            "NONE",
            "severity_below_threshold",
        )
    if context.environment != "production":
        return _decision(
            CorrelationOutcome.REJECTED_INELIGIBLE,
            "NONE",
            "non_production_environment",
        )
    if context.catalog_state is None:
        return _decision(
            CorrelationOutcome.REJECTED_INELIGIBLE,
            "NONE",
            "service_not_registered",
        )
    if context.catalog_state is CatalogState.INACTIVE:
        return _decision(
            CorrelationOutcome.REJECTED_INELIGIBLE,
            "NONE",
            "service_inactive",
        )

    exact_candidates = context.exact_candidate_ids
    if len(exact_candidates) > 20:
        return _decision(
            CorrelationOutcome.CREATED_AMBIGUOUS,
            "CREATE",
            "candidate_limit_reached",
            candidate_incident_ids=exact_candidates[:20],
        )
    if len(exact_candidates) == 1:
        return _decision(
            CorrelationOutcome.LINKED_EXACT_SERVICE,
            "LINK",
            "one_exact_service_candidate",
            selected_incident_id=exact_candidates[0],
            candidate_incident_ids=exact_candidates,
        )
    if exact_candidates:
        return _decision(
            CorrelationOutcome.CREATED_AMBIGUOUS,
            "CREATE",
            "multiple_exact_service_candidates",
            candidate_incident_ids=exact_candidates,
        )

    dependency_candidates = context.dependency_candidate_ids
    if dependency_candidates:
        reason: CorrelationReasonCode = "dependency_symptom_candidate"
        if len(dependency_candidates) > 20:
            reason = "candidate_limit_reached"
        return _decision(
            CorrelationOutcome.CREATED_DEPENDENCY_CANDIDATE,
            "CREATE",
            reason,
            candidate_incident_ids=dependency_candidates[:20],
        )

    return _decision(
        CorrelationOutcome.CREATED_NO_MATCH,
        "CREATE",
        "no_same_service_candidate",
    )
