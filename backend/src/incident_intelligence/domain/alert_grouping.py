# ruff: noqa: RUF001

from __future__ import annotations

import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from incident_intelligence.domain.enums import CatalogState
from incident_intelligence.domain.models import Environment, ServiceName, UtcAwareDatetime

GroupingAction = Literal["CREATE_GROUP", "JOIN_GROUP", "KEEP_GROUP"]
GroupingReasonCode = Literal[
    "alert_already_grouped",
    "same_service_environment_symptom_window",
    "no_eligible_group",
    "multiple_eligible_groups",
    "service_not_registered",
    "service_inactive",
    "symptom_unknown",
    "candidate_outside_window",
    "candidate_incident_conflict",
]
ResourceType = Literal["pod", "instance", "node", "container", "alert"]
AlertId = Annotated[str, StringConstraints(pattern=r"^alt_[0-9a-f]{32}$")]
AlertGroupId = Annotated[str, StringConstraints(pattern=r"^agr_[0-9a-f]{32}$")]
IncidentId = Annotated[str, StringConstraints(pattern=r"^inc_[0-9a-f]{32}$")]
Symptom = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]

GROUPING_WINDOW_SECONDS = 120
SAFE_RESOURCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RESOURCE_FIELDS: tuple[tuple[ResourceType, tuple[str, ...]], ...] = (
    ("pod", ("pod", "pod_name", "kubernetes_pod_name")),
    ("instance", ("instance", "instance_name")),
    ("node", ("node", "node_name")),
    ("container", ("container", "container_name")),
)
ALERT_ID_ADAPTER = TypeAdapter(AlertId)

EXPLANATIONS: dict[GroupingReasonCode, str] = {
    "alert_already_grouped": "该告警轮次已经属于现有告警组，保持原成员关系。",
    "same_service_environment_symptom_window": (
        "服务、环境和症状一致，且位于 120 秒活动窗口内，已归入现有告警组。"
    ),
    "no_eligible_group": "没有唯一且满足安全条件的现有告警组，已建立独立告警组。",
    "multiple_eligible_groups": "同时存在多个符合条件的告警组，为避免误合并已建立独立告警组。",
    "service_not_registered": "服务尚未登记，无法安全自动归组，已建立独立告警组。",
    "service_inactive": "服务目录项已停用，无法安全自动归组，已建立独立告警组。",
    "symptom_unknown": "告警症状无法识别，已建立独立告警组。",
    "candidate_outside_window": "相似告警组已超出 120 秒活动窗口，已建立独立告警组。",
    "candidate_incident_conflict": "相似告警已属于不同事故，为避免跨事故误并已建立独立告警组。",
}


class AlertGroupCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: AlertGroupId
    service: ServiceName
    environment: Environment
    symptom: Symptom
    last_observed_at: UtcAwareDatetime
    incident_id: IncidentId | None = None


class GroupingContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    alert_id: AlertId
    service: ServiceName
    environment: Environment
    symptom: Symptom
    observed_at: UtcAwareDatetime
    catalog_state: CatalogState | None
    existing_group_id: AlertGroupId | None = None
    alert_incident_id: IncidentId | None = None
    candidates: tuple[AlertGroupCandidate, ...] = Field(default=(), max_length=21)


class GroupingDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: GroupingAction
    selected_group_id: AlertGroupId | None = None
    candidate_group_ids: tuple[AlertGroupId, ...] = Field(default=(), max_length=20)
    reason_codes: tuple[GroupingReasonCode, ...] = Field(min_length=1, max_length=10)
    explanation: str = Field(min_length=1, max_length=500)


class ResourceIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    resource_type: ResourceType
    resource_name: str = Field(min_length=1, max_length=128)
    resource_key: str = Field(pattern=r"^[0-9a-f]{64}$")


def decide_alert_group(context: GroupingContext) -> GroupingDecision:
    if context.existing_group_id is not None:
        return _decision("KEEP_GROUP", "alert_already_grouped", context.existing_group_id)
    if context.catalog_state is None:
        return _decision("CREATE_GROUP", "service_not_registered")
    if context.catalog_state is CatalogState.INACTIVE:
        return _decision("CREATE_GROUP", "service_inactive")
    if context.symptom.casefold() == "unknown":
        return _decision("CREATE_GROUP", "symptom_unknown")

    exact_candidates = tuple(
        item
        for item in context.candidates
        if item.service == context.service
        and item.environment == context.environment
        and item.symptom == context.symptom
    )
    compatible_candidates = tuple(
        item for item in exact_candidates if _incident_is_compatible(context, item)
    )
    eligible_candidates = tuple(
        item for item in compatible_candidates if _is_in_window(context, item)
    )

    if len(eligible_candidates) == 1:
        item = eligible_candidates[0]
        return _decision(
            "JOIN_GROUP",
            "same_service_environment_symptom_window",
            item.id,
            (item.id,),
        )
    if len(eligible_candidates) > 1:
        return _decision(
            "CREATE_GROUP",
            "multiple_eligible_groups",
            candidate_group_ids=tuple(item.id for item in eligible_candidates[:20]),
        )
    if exact_candidates and not compatible_candidates:
        return _decision("CREATE_GROUP", "candidate_incident_conflict")
    if compatible_candidates:
        return _decision("CREATE_GROUP", "candidate_outside_window")
    return _decision("CREATE_GROUP", "no_eligible_group")


def derive_resource_identity(
    facts: Mapping[str, str],
    alert_id: str,
) -> ResourceIdentity:
    validated_alert_id = ALERT_ID_ADAPTER.validate_python(alert_id)
    for resource_type, field_names in RESOURCE_FIELDS:
        for field_name in field_names:
            raw_name = facts.get(field_name)
            if not isinstance(raw_name, str) or not raw_name:
                continue
            digest = _resource_digest(resource_type, raw_name)
            display_name = raw_name
            if SAFE_RESOURCE_NAME.fullmatch(raw_name) is None:
                display_name = f"{resource_type}-{digest[:12]}"
            return ResourceIdentity(
                resource_type=resource_type,
                resource_name=display_name,
                resource_key=digest,
            )

    identity = ResourceIdentity(
        resource_type="alert",
        resource_name=validated_alert_id,
        resource_key=_resource_digest("alert", validated_alert_id),
    )
    return identity


def _decision(
    action: GroupingAction,
    reason: GroupingReasonCode,
    selected_group_id: str | None = None,
    candidate_group_ids: tuple[str, ...] = (),
) -> GroupingDecision:
    return GroupingDecision(
        action=action,
        selected_group_id=selected_group_id,
        candidate_group_ids=candidate_group_ids,
        reason_codes=(reason,),
        explanation=EXPLANATIONS[reason],
    )


def _incident_is_compatible(
    context: GroupingContext,
    candidate: AlertGroupCandidate,
) -> bool:
    return (
        context.alert_incident_id is None
        or candidate.incident_id is None
        or context.alert_incident_id == candidate.incident_id
    )


def _is_in_window(context: GroupingContext, candidate: AlertGroupCandidate) -> bool:
    age_seconds = (context.observed_at - candidate.last_observed_at).total_seconds()
    return 0 <= age_seconds <= GROUPING_WINDOW_SECONDS


def _resource_digest(resource_type: str, resource_name: str) -> str:
    return sha256(f"{resource_type}\0{resource_name}".encode()).hexdigest()
