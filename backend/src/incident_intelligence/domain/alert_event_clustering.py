# ruff: noqa: RUF001

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field

from incident_intelligence.domain.alert_event_profiles import AlertEventProfile
from incident_intelligence.domain.models import Environment, ServiceName, UtcAwareDatetime
from incident_intelligence.domain.problem_signatures import ProblemScopeType
from incident_intelligence.services.text_similarity import CombinedTextSimilarity

AlertId = Annotated[str, StringConstraints(pattern=r"^alt_[0-9a-f]{32}$")]
AlertGroupId = Annotated[str, StringConstraints(pattern=r"^agr_[0-9a-f]{32}$")]
HistorySignal = Literal["NONE", "POSITIVE", "NEGATIVE"]
MembershipOutcome = Literal["AUTO_JOIN", "PENDING_CONFIRMATION", "CREATE_EVENT"]


class CandidateContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    alert_id: AlertId
    environment: Environment
    service: ServiceName | None
    entity_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_type: ProblemScopeType
    problem_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    problem_type: str = Field(min_length=1, max_length=200)
    symptom: str = Field(min_length=1, max_length=64)
    observed_at: UtcAwareDatetime
    topology_distance: int | None = Field(default=None, ge=0, le=100)
    matched_member_ids: tuple[AlertId, ...] = Field(default=(), max_length=50)
    text_similarity: CombinedTextSimilarity
    history_signal: HistorySignal = "NONE"


class CandidateScore(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    group_id: AlertGroupId
    entity_service_score: int = Field(ge=0, le=35)
    topology_score: int = Field(ge=0, le=25)
    temporal_score: int = Field(ge=0, le=20)
    semantic_score: int = Field(ge=0, le=15)
    history_score: int = Field(ge=0, le=5)
    strong_anchor: bool
    hard_exclusions: tuple[str, ...] = Field(max_length=10)
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=10)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_score(self) -> int:
        return min(
            100,
            self.entity_service_score
            + self.topology_score
            + self.temporal_score
            + self.semantic_score
            + self.history_score,
        )


class MembershipDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: MembershipOutcome
    selected_group_id: AlertGroupId | None = None
    candidate_group_ids: tuple[AlertGroupId, ...] = Field(default=(), max_length=50)
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=10)
    explanation: str = Field(min_length=1, max_length=500)


def score_candidate(
    context: CandidateContext,
    profile: AlertEventProfile,
) -> CandidateScore:
    same_entity = context.entity_key in profile.entity_keys
    same_service = context.service is not None and context.service in profile.services
    same_problem = context.problem_key in profile.problem_keys
    core_anchor = (
        context.entity_key in profile.core_entity_keys
        or (context.service is not None and context.service in profile.core_services)
        or context.problem_type in profile.core_problem_types
    )
    matched_core_member = bool(
        set(context.matched_member_ids).intersection(profile.core_member_ids)
    )

    hard_exclusions: list[str] = []
    if context.environment != profile.environment:
        hard_exclusions.append("environment_mismatch")
    if context.topology_distance is not None and context.topology_distance > 2:
        hard_exclusions.append("topology_depth_exceeded")
    if context.history_signal == "NEGATIVE":
        hard_exclusions.append("negative_history_match")
    if (
        context.matched_member_ids
        and not matched_core_member
        and not core_anchor
        and not same_problem
    ):
        hard_exclusions.append("edge_member_only")

    direct_service_relation = context.topology_distance == 1
    entity_service_score = (
        35 if same_entity or same_service or same_problem else 25 if direct_service_relation else 0
    )
    topology_score = _topology_score(context.topology_distance)
    temporal_score = _temporal_score(context, profile)
    semantic_score = min(
        15,
        (9 if context.problem_type in profile.problem_types else 0)
        + (9 if context.symptom in profile.symptoms else 0)
        + round(context.text_similarity.score * 7),
    )
    history_score = 5 if context.history_signal == "POSITIVE" else 0
    strong_anchor = (
        same_entity
        or same_service
        or same_problem
        or (context.topology_distance is not None and context.topology_distance <= 1)
    )

    reason_codes = tuple(
        code
        for condition, code in (
            (same_entity, "same_entity"),
            (same_service, "same_service"),
            (same_problem, "same_problem_signature"),
            (context.topology_distance == 1, "direct_topology_relation"),
            (context.topology_distance == 2, "two_hop_topology_relation"),
            (semantic_score > 0, "semantic_features_matched"),
            (context.history_signal == "POSITIVE", "positive_history_match"),
        )
        if condition
    ) or ("no_positive_match",)
    return CandidateScore(
        group_id=profile.group_id,
        entity_service_score=entity_service_score,
        topology_score=topology_score,
        temporal_score=temporal_score,
        semantic_score=semantic_score,
        history_score=history_score,
        strong_anchor=strong_anchor,
        hard_exclusions=tuple(hard_exclusions),
        reason_codes=reason_codes,
    )


def decide_membership(
    scores: tuple[CandidateScore, ...], *, candidates_truncated: bool = False
) -> MembershipDecision:
    eligible = tuple(item for item in scores if not item.hard_exclusions)
    ranked = tuple(sorted(eligible, key=lambda item: (-item.total_score, item.group_id)))
    candidate_ids = tuple(item.group_id for item in ranked[:50])
    if candidates_truncated:
        return _decision("PENDING_CONFIRMATION", None, candidate_ids, "candidate_limit_exceeded")
    if not ranked:
        return _decision("CREATE_EVENT", None, (), "no_eligible_event")
    best = ranked[0]
    if best.total_score < 50:
        return _decision("CREATE_EVENT", None, candidate_ids, "score_below_create_threshold")
    if not best.strong_anchor:
        return _decision("CREATE_EVENT", None, candidate_ids, "strong_anchor_missing")
    if len(ranked) > 1 and best.total_score - ranked[1].total_score < 10:
        return _decision("PENDING_CONFIRMATION", None, candidate_ids, "candidate_scores_too_close")
    if best.total_score < 70:
        return _decision(
            "PENDING_CONFIRMATION", None, candidate_ids, "medium_confidence_event_match"
        )
    return _decision("AUTO_JOIN", best.group_id, candidate_ids, "high_confidence_event_match")


def _topology_score(distance: int | None) -> int:
    if distance is None:
        return 0
    if distance <= 1:
        return 25
    if distance == 2:
        return 15
    return 0


def _temporal_score(context: CandidateContext, profile: AlertEventProfile) -> int:
    age = abs((context.observed_at - profile.last_observed_at).total_seconds())
    if age <= 120:
        return 20
    if age <= 300:
        return 15
    if age <= 900:
        return 8
    return 0


def _decision(
    outcome: MembershipOutcome,
    selected_group_id: str | None,
    candidate_group_ids: tuple[str, ...],
    reason_code: str,
) -> MembershipDecision:
    explanations = {
        "no_eligible_event": "没有满足安全条件的候选事件，建立新的告警事件。",
        "score_below_create_threshold": "最高候选得分低于五十分，保持为独立告警事件。",
        "strong_anchor_missing": "候选缺少实体、服务、问题签名或直接拓扑强锚点，不能自动归集。",
        "candidate_scores_too_close": "最高两个候选得分差不足十分，需要人工确认归属。",
        "candidate_limit_exceeded": "候选事件超过单次安全比较上限，需要人工确认归属。",
        "medium_confidence_event_match": "候选得分处于中置信区间，需要人工确认归属。",
        "high_confidence_event_match": "候选具有强锚点且得分达到自动归集阈值。",
    }
    return MembershipDecision(
        outcome=outcome,
        selected_group_id=selected_group_id,
        candidate_group_ids=candidate_group_ids,
        reason_codes=(reason_code,),
        explanation=explanations[reason_code],
    )
