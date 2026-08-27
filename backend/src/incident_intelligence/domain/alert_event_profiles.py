from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from incident_intelligence.domain.alert_text_similarity import (
    NormalizedAlertText,
    deterministic_similarity,
)
from incident_intelligence.domain.models import Environment, ServiceName, UtcAwareDatetime
from incident_intelligence.domain.problem_signatures import ProblemScopeType

AlertId = Annotated[str, StringConstraints(pattern=r"^alt_[0-9a-f]{32}$")]
AlertGroupId = Annotated[str, StringConstraints(pattern=r"^agr_[0-9a-f]{32}$")]
MembershipState = Literal["AUTO_CONFIRMED", "MANUAL_CONFIRMED"]


class ConfirmedEventMember(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    alert_id: AlertId
    membership_state: MembershipState
    environment: Environment
    service: ServiceName | None
    entity_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_type: ProblemScopeType
    problem_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    problem_type: str = Field(min_length=1, max_length=200)
    symptom: str = Field(min_length=1, max_length=64)
    topology_nodes: tuple[str, ...] = Field(default=(), max_length=50)
    normalized_text: NormalizedAlertText
    observed_at: UtcAwareDatetime


class AlertEventProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    group_id: AlertGroupId
    environment: Environment
    services: tuple[ServiceName, ...] = Field(max_length=100)
    entity_keys: tuple[str, ...] = Field(max_length=1_000)
    scope_types: tuple[ProblemScopeType, ...] = Field(max_length=6)
    problem_keys: tuple[str, ...] = Field(max_length=1_000)
    problem_types: tuple[str, ...] = Field(max_length=100)
    symptoms: tuple[str, ...] = Field(max_length=100)
    topology_nodes: tuple[str, ...] = Field(max_length=2_000)
    normalized_text: NormalizedAlertText
    member_ids: tuple[AlertId, ...] = Field(min_length=1, max_length=1_000)
    core_member_ids: tuple[AlertId, ...] = Field(max_length=1_000)
    core_services: tuple[ServiceName, ...] = Field(max_length=100)
    core_problem_types: tuple[str, ...] = Field(max_length=100)
    core_entity_keys: tuple[str, ...] = Field(max_length=1_000)
    first_observed_at: UtcAwareDatetime
    last_observed_at: UtcAwareDatetime
    auto_confirmed_count: int = Field(ge=0, le=1_000)
    manual_confirmed_count: int = Field(ge=0, le=1_000)
    profile_version: int = Field(default=1, ge=1)
    rule_version: Literal["alert-event-profile.v1"] = "alert-event-profile.v1"


def build_event_profile(
    group_id: str,
    confirmed_members: tuple[ConfirmedEventMember, ...],
    *,
    profile_version: int = 1,
) -> AlertEventProfile:
    if not confirmed_members:
        raise ValueError("event_profile_requires_confirmed_member")
    members = tuple(sorted(confirmed_members, key=lambda item: item.alert_id))
    environments = {item.environment for item in members}
    if len(environments) != 1:
        raise ValueError("event_profile_environment_mismatch")

    threshold = 1 if len(members) == 1 else max(2, (len(members) + 1) // 2)
    service_counts = Counter(item.service for item in members if item.service is not None)
    problem_counts = Counter(item.problem_type for item in members)
    entity_counts = Counter(item.entity_key for item in members)
    core_services = tuple(
        sorted(key for key, count in service_counts.items() if count >= threshold)
    )
    core_problem_types = tuple(
        sorted(key for key, count in problem_counts.items() if count >= threshold)
    )
    core_entity_keys = tuple(
        sorted(key for key, count in entity_counts.items() if count >= threshold)
    )
    core_member_ids = tuple(
        item.alert_id
        for item in members
        if item.service in core_services
        or item.problem_type in core_problem_types
        or item.entity_key in core_entity_keys
    )

    return AlertEventProfile(
        group_id=group_id,
        environment=members[0].environment,
        services=_bounded_unique(item.service for item in members if item.service is not None),
        entity_keys=_bounded_unique((item.entity_key for item in members), limit=1_000),
        scope_types=_bounded_unique((item.scope_type for item in members), limit=6),
        problem_keys=_bounded_unique((item.problem_key for item in members), limit=1_000),
        problem_types=_bounded_unique(item.problem_type for item in members),
        symptoms=_bounded_unique(item.symptom for item in members),
        topology_nodes=_bounded_unique(
            (node for item in members for node in item.topology_nodes), limit=2_000
        ),
        normalized_text=_text_center(members),
        member_ids=tuple(item.alert_id for item in members),
        core_member_ids=core_member_ids,
        core_services=core_services,
        core_problem_types=core_problem_types,
        core_entity_keys=core_entity_keys,
        first_observed_at=min(item.observed_at for item in members),
        last_observed_at=max(item.observed_at for item in members),
        auto_confirmed_count=sum(item.membership_state == "AUTO_CONFIRMED" for item in members),
        manual_confirmed_count=sum(item.membership_state == "MANUAL_CONFIRMED" for item in members),
        profile_version=profile_version,
    )


def _text_center(members: tuple[ConfirmedEventMember, ...]) -> NormalizedAlertText:
    def distance_key(member: ConfirmedEventMember) -> tuple[float, str]:
        similarity = sum(
            deterministic_similarity(member.normalized_text, other.normalized_text)
            for other in members
        )
        return (-similarity, member.alert_id)

    return min(members, key=distance_key).normalized_text


def _bounded_unique[BoundedValue: str](
    values: Iterable[BoundedValue], *, limit: int = 100
) -> tuple[BoundedValue, ...]:
    return tuple(sorted(set(values)))[:limit]
