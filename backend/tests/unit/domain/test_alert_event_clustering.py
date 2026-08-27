from datetime import UTC, datetime, timedelta

from incident_intelligence.domain.alert_event_clustering import (
    CandidateContext,
    CandidateScore,
    decide_membership,
    score_candidate,
)
from incident_intelligence.domain.alert_event_profiles import AlertEventProfile
from incident_intelligence.domain.alert_text_similarity import normalize_alert_text
from incident_intelligence.services.text_similarity import CombinedTextSimilarity

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
GROUP_A = "agr_" + "1" * 32
GROUP_B = "agr_" + "2" * 32
ALERT_A = "alt_" + "a" * 32


def text_score(score: float) -> CombinedTextSimilarity:
    return CombinedTextSimilarity(
        score=score,
        deterministic_score=score,
        semantic_score=None,
        provider_status="DISABLED",
    )


def profile(**overrides: object) -> AlertEventProfile:
    values: dict[str, object] = {
        "group_id": GROUP_A,
        "environment": "production",
        "services": ("payment-api",),
        "entity_keys": ("e" * 64,),
        "scope_types": ("SERVICE",),
        "problem_keys": ("a" * 64,),
        "problem_types": ("PaymentHighErrorRate",),
        "symptoms": ("errors",),
        "topology_nodes": ("payment-api", "business-mysql"),
        "normalized_text": normalize_alert_text(
            "支付错误率升高", "HTTP 5xx 超过阈值", "PaymentHighErrorRate", "errors"
        ),
        "member_ids": (ALERT_A,),
        "core_member_ids": (ALERT_A,),
        "core_services": ("payment-api",),
        "core_problem_types": ("PaymentHighErrorRate",),
        "core_entity_keys": ("e" * 64,),
        "first_observed_at": NOW - timedelta(minutes=2),
        "last_observed_at": NOW - timedelta(seconds=20),
        "auto_confirmed_count": 1,
        "manual_confirmed_count": 0,
        "profile_version": 1,
        "rule_version": "alert-event-profile.v1",
    }
    values.update(overrides)
    return AlertEventProfile.model_validate(values)


def context(**overrides: object) -> CandidateContext:
    values: dict[str, object] = {
        "alert_id": "alt_" + "b" * 32,
        "environment": "production",
        "service": "payment-api",
        "entity_key": "e" * 64,
        "scope_type": "SERVICE",
        "problem_key": "a" * 64,
        "problem_type": "PaymentHighErrorRate",
        "symptom": "errors",
        "observed_at": NOW,
        "topology_distance": 1,
        "matched_member_ids": (ALERT_A,),
        "text_similarity": text_score(1.0),
        "history_signal": "NONE",
    }
    values.update(overrides)
    return CandidateContext.model_validate(values)


def candidate(group_id: str, total: int, *, strong_anchor: bool = True) -> CandidateScore:
    return CandidateScore(
        group_id=group_id,
        entity_service_score=min(total, 35),
        topology_score=min(max(total - 35, 0), 25),
        temporal_score=min(max(total - 60, 0), 20),
        semantic_score=min(max(total - 80, 0), 15),
        history_score=min(max(total - 95, 0), 5),
        strong_anchor=strong_anchor,
        hard_exclusions=(),
        reason_codes=("test_candidate",),
    )


def test_high_score_with_strong_anchor_auto_joins() -> None:
    score = score_candidate(context(), profile())
    decision = decide_membership((score,))

    assert score.total_score == 95
    assert score.strong_anchor is True
    assert decision.outcome == "AUTO_JOIN"
    assert decision.selected_group_id == GROUP_A
    assert decision.reason_codes == ("high_confidence_event_match",)


def test_threshold_boundaries_are_deterministic() -> None:
    at_seventy = decide_membership((candidate(GROUP_A, 70),))
    at_fifty = decide_membership((candidate(GROUP_A, 50),))
    below_fifty = decide_membership((candidate(GROUP_A, 49),))

    assert at_seventy.outcome == "AUTO_JOIN"
    assert at_fifty.outcome == "PENDING_CONFIRMATION"
    assert below_fifty.outcome == "CREATE_EVENT"


def test_close_top_two_candidates_require_confirmation_with_stable_order() -> None:
    decision = decide_membership((candidate(GROUP_B, 78), candidate(GROUP_A, 78)))

    assert decision.outcome == "PENDING_CONFIRMATION"
    assert decision.candidate_group_ids == (GROUP_A, GROUP_B)
    assert decision.reason_codes == ("candidate_scores_too_close",)


def test_truncated_candidate_set_never_auto_selects() -> None:
    decision = decide_membership((candidate(GROUP_A, 95),), candidates_truncated=True)

    assert decision.outcome == "PENDING_CONFIRMATION"
    assert decision.selected_group_id is None
    assert decision.reason_codes == ("candidate_limit_exceeded",)


def test_text_only_similarity_never_auto_joins() -> None:
    score = score_candidate(
        context(
            service="other-api",
            entity_key="d" * 64,
            problem_key="c" * 64,
            problem_type="OtherFailure",
            symptom="latency",
            topology_distance=None,
            matched_member_ids=(),
            text_similarity=text_score(0.96),
        ),
        profile(),
    )

    assert score.strong_anchor is False
    assert decide_membership((score,)).outcome == "CREATE_EVENT"


def test_direct_service_dependency_can_reach_auto_join_threshold() -> None:
    score = score_candidate(
        context(
            service="payment-api",
            entity_key="d" * 64,
            problem_key="c" * 64,
            problem_type="PaymentLatencyHigh",
            symptom="latency",
            topology_distance=1,
            matched_member_ids=(ALERT_A,),
            text_similarity=text_score(0.0),
        ),
        profile(services=("business-mysql",), core_services=("business-mysql",)),
    )

    assert score.entity_service_score == 25
    assert score.topology_score == 25
    assert score.temporal_score == 20
    assert score.total_score == 70
    assert decide_membership((score,)).outcome == "AUTO_JOIN"


def test_cross_environment_and_topology_over_two_hops_are_hard_exclusions() -> None:
    cross_environment = score_candidate(context(environment="staging"), profile())
    too_far = score_candidate(context(topology_distance=3), profile())

    assert cross_environment.hard_exclusions == ("environment_mismatch",)
    assert too_far.hard_exclusions == ("topology_depth_exceeded",)
    assert decide_membership((cross_environment, too_far)).outcome == "CREATE_EVENT"


def test_matching_only_an_edge_member_cannot_expand_event_chain() -> None:
    edge_member = "alt_" + "c" * 32
    score = score_candidate(
        context(
            service="edge-api",
            entity_key="d" * 64,
            problem_key="c" * 64,
            problem_type="EdgeTimeout",
            symptom="latency",
            topology_distance=2,
            matched_member_ids=(edge_member,),
        ),
        profile(
            member_ids=(ALERT_A, edge_member),
            core_member_ids=(ALERT_A,),
            topology_nodes=("payment-api", "edge-api"),
        ),
    )

    assert score.hard_exclusions == ("edge_member_only",)
    assert decide_membership((score,)).outcome == "CREATE_EVENT"


def test_positive_history_adds_five_points_and_negative_history_excludes() -> None:
    positive = score_candidate(context(history_signal="POSITIVE"), profile())
    neutral = score_candidate(context(history_signal="NONE"), profile())
    negative = score_candidate(context(history_signal="NEGATIVE"), profile())

    assert positive.history_score == 5
    assert positive.total_score == min(100, neutral.total_score + 5)
    assert negative.hard_exclusions == ("negative_history_match",)
