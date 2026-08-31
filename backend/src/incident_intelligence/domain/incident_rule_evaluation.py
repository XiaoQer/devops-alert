from __future__ import annotations

from collections import defaultdict, deque
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.domain.incident_rules import (
    IncidentRuleCondition,
    IncidentRuleConfig,
)
from incident_intelligence.domain.models import (
    AlertName,
    Environment,
    ServiceName,
    Severity,
    UtcAwareDatetime,
)

AlertEvaluationState = Literal["ACTIVE", "RESOLVED"]
SEVERITY_RANK: dict[Severity, int] = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


class _FrozenEvaluationModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AlertEvaluationFact(_FrozenEvaluationModel):
    id: str = Field(min_length=1, max_length=36)
    alert_source_id: str = Field(pattern=r"^src_[0-9a-f]{32}$")
    alert_name: AlertName
    state: AlertEvaluationState
    severity: Severity
    environment: Environment
    service: ServiceName | None
    entity_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    entity_display_name: str = Field(min_length=1, max_length=257)
    first_received_at: UtcAwareDatetime


class EvaluationMatch(_FrozenEvaluationModel):
    group_key: str = Field(min_length=1, max_length=257)
    group_display_name: str = Field(min_length=1, max_length=257)
    window_start: UtcAwareDatetime
    window_end: UtcAwareDatetime
    alert_count: int = Field(ge=1)
    active_alert_count: int = Field(ge=0)
    distinct_alert_names: int = Field(ge=1)
    distinct_entities: int = Field(ge=1)
    highest_severity: Severity
    example_alert_ids: tuple[str, ...] = Field(max_length=10)


class EvaluationResult(_FrozenEvaluationModel):
    scanned_alert_count: int = Field(ge=0)
    matches: tuple[EvaluationMatch, ...]
    truncated: bool


def evaluate_rule(
    config: IncidentRuleConfig,
    alerts: tuple[AlertEvaluationFact, ...],
    *,
    max_matches: int = 100,
) -> EvaluationResult:
    if max_matches < 1:
        raise ValueError("max_matches 必须大于零")
    groups: dict[str, list[AlertEvaluationFact]] = defaultdict(list)
    for alert in alerts:
        group_key = _matching_group(config, alert)
        if group_key is not None:
            groups[group_key].append(alert)

    matches: list[EvaluationMatch] = []
    truncated = False
    for group_key in sorted(groups):
        group_alerts = sorted(
            groups[group_key],
            key=lambda item: (item.first_received_at, item.id),
        )
        window: deque[AlertEvaluationFact] = deque()
        previous_member_ids: tuple[str, ...] | None = None
        for anchor in group_alerts:
            lower_boundary = anchor.first_received_at - timedelta(minutes=config.window_minutes)
            while window and window[0].first_received_at <= lower_boundary:
                window.popleft()
            window.append(anchor)
            unique_members = {
                member.id: member
                for member in sorted(window, key=lambda item: (item.first_received_at, item.id))
            }
            member_ids = tuple(unique_members)
            members = tuple(unique_members.values())
            if member_ids == previous_member_ids or not _all_conditions_match(
                config.conditions,
                members,
            ):
                continue
            previous_member_ids = member_ids
            if len(matches) >= max_matches:
                truncated = True
                break
            matches.append(
                _match(
                    config=config,
                    group_key=group_key,
                    anchor=anchor,
                    members=members,
                )
            )
        if truncated:
            break
    return EvaluationResult(
        scanned_alert_count=len(alerts),
        matches=tuple(matches),
        truncated=truncated,
    )


def _matching_group(
    config: IncidentRuleConfig,
    alert: AlertEvaluationFact,
) -> str | None:
    if alert.environment != config.environment:
        return None
    if config.alert_source_ids and alert.alert_source_id not in config.alert_source_ids:
        return None
    if config.services and alert.service not in config.services:
        return None
    if config.group_by == "SERVICE":
        return alert.service
    return alert.entity_key


def _all_conditions_match(
    conditions: tuple[IncidentRuleCondition, ...],
    members: tuple[AlertEvaluationFact, ...],
) -> bool:
    return all(_condition_matches(condition, members) for condition in conditions)


def _condition_matches(
    condition: IncidentRuleCondition,
    members: tuple[AlertEvaluationFact, ...],
) -> bool:
    if condition.type == "DISTINCT_ALERT_NAMES_GTE":
        return len({member.alert_name for member in members}) >= condition.threshold
    if condition.type == "ACTIVE_ALERTS_GTE":
        return sum(member.state == "ACTIVE" for member in members) >= condition.threshold
    if condition.type == "DISTINCT_ENTITIES_GTE":
        return len({member.entity_key for member in members}) >= condition.threshold
    highest = max(SEVERITY_RANK[member.severity] for member in members)
    return highest >= SEVERITY_RANK[condition.severity]


def _match(
    *,
    config: IncidentRuleConfig,
    group_key: str,
    anchor: AlertEvaluationFact,
    members: tuple[AlertEvaluationFact, ...],
) -> EvaluationMatch:
    highest = max(members, key=lambda item: SEVERITY_RANK[item.severity]).severity
    display_name = (
        group_key
        if config.group_by == "SERVICE"
        else next(item.entity_display_name for item in members if item.entity_key == group_key)
    )
    return EvaluationMatch(
        group_key=group_key,
        group_display_name=display_name,
        window_start=anchor.first_received_at - timedelta(minutes=config.window_minutes),
        window_end=anchor.first_received_at,
        alert_count=len(members),
        active_alert_count=sum(member.state == "ACTIVE" for member in members),
        distinct_alert_names=len({member.alert_name for member in members}),
        distinct_entities=len({member.entity_key for member in members}),
        highest_severity=highest,
        example_alert_ids=tuple(member.id for member in members[:10]),
    )
