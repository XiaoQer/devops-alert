# ruff: noqa: RUF001

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from incident_intelligence.domain.alert_grouping import (
    AlertGroupCandidate,
    GroupingContext,
    decide_alert_group,
    derive_resource_identity,
)

NOW = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
ALERT_ID = "alt_" + "a" * 32
GROUP_1 = "agr_" + "1" * 32
GROUP_2 = "agr_" + "2" * 32
INCIDENT_1 = "inc_" + "1" * 32
INCIDENT_2 = "inc_" + "2" * 32


def candidate(**overrides: object) -> AlertGroupCandidate:
    values: dict[str, object] = {
        "id": GROUP_1,
        "service": "payment-api",
        "problem_key": "a" * 64,
        "environment": "production",
        "symptom": "errors",
        "last_observed_at": NOW - timedelta(seconds=30),
        "incident_id": None,
    }
    values.update(overrides)
    return AlertGroupCandidate.model_validate(values)


def context(**overrides: object) -> GroupingContext:
    values: dict[str, object] = {
        "alert_id": ALERT_ID,
        "service": "payment-api",
        "problem_key": "a" * 64,
        "window_seconds": 300,
        "environment": "production",
        "symptom": "errors",
        "observed_at": NOW,
        "catalog_state": "ACTIVE",
        "existing_group_id": None,
        "alert_incident_id": None,
        "candidates": (),
    }
    values.update(overrides)
    return GroupingContext.model_validate(values)


def test_unique_compatible_candidate_is_joined() -> None:
    decision = decide_alert_group(context(candidates=(candidate(),)))

    assert decision.action == "JOIN_GROUP"
    assert decision.selected_group_id == GROUP_1
    assert decision.reason_codes == ("same_service_environment_symptom_window",)
    assert (
        decision.explanation == "服务、环境和症状一致，且位于配置的活动窗口内，已归入现有告警组。"
    )


def test_out_of_order_processing_within_window_still_converges() -> None:
    decision = decide_alert_group(
        context(candidates=(candidate(last_observed_at=NOW + timedelta(seconds=30)),))
    )

    assert decision.action == "JOIN_GROUP"
    assert decision.selected_group_id == GROUP_1


def test_existing_membership_is_kept_before_reconsidering_candidates() -> None:
    decision = decide_alert_group(
        context(
            existing_group_id=GROUP_2,
            catalog_state=None,
            symptom="unknown",
            candidates=(candidate(),),
        )
    )

    assert decision.action == "KEEP_GROUP"
    assert decision.selected_group_id == GROUP_2
    assert decision.reason_codes == ("alert_already_grouped",)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"candidates": ()}, "no_eligible_group"),
        ({"catalog_state": None}, "service_not_registered"),
        ({"catalog_state": "INACTIVE"}, "service_inactive"),
        ({"symptom": "unknown"}, "no_eligible_group"),
        (
            {"candidates": (candidate(last_observed_at=NOW - timedelta(seconds=301)),)},
            "candidate_outside_window",
        ),
        (
            {
                "candidates": (candidate(incident_id=INCIDENT_2),),
                "alert_incident_id": INCIDENT_1,
            },
            "candidate_incident_conflict",
        ),
        ({"candidates": (candidate(environment="staging"),)}, "no_eligible_group"),
        (
            {"candidates": (candidate(service="order-api", problem_key="b" * 64),)},
            "no_eligible_group",
        ),
        ({"candidates": (candidate(symptom="latency"),)}, "no_eligible_group"),
    ],
)
def test_unsafe_or_unmatched_cases_create_independent_group(
    overrides: dict[str, object], reason: str
) -> None:
    decision = decide_alert_group(context(**overrides))

    assert decision.action == "CREATE_GROUP"
    assert decision.selected_group_id is None
    assert decision.reason_codes == (reason,)
    assert 1 <= len(decision.explanation) <= 500


def test_multiple_eligible_candidates_never_auto_merge() -> None:
    decision = decide_alert_group(context(candidates=(candidate(), candidate(id=GROUP_2))))

    assert decision.action == "CREATE_GROUP"
    assert decision.selected_group_id is None
    assert decision.candidate_group_ids == (GROUP_1, GROUP_2)
    assert decision.reason_codes == ("multiple_eligible_groups",)
    assert decision.explanation == "同时存在多个符合条件的告警组，为避免误合并已建立独立告警组。"


def test_source_is_not_a_grouping_key() -> None:
    decision = decide_alert_group(context(candidates=(candidate(),)))

    assert decision.action == "JOIN_GROUP"
    assert not hasattr(context(), "source")


def test_service_missing_alert_joins_matching_problem_candidate() -> None:
    decision = decide_alert_group(
        GroupingContext.model_validate(
            {
                "alert_id": ALERT_ID,
                "service": None,
                "problem_key": "e" * 64,
                "window_seconds": 300,
                "environment": "unknown",
                "symptom": "pod_not_ready",
                "observed_at": NOW,
                "catalog_state": None,
                "candidates": (
                    {
                        "id": GROUP_1,
                        "service": None,
                        "problem_key": "e" * 64,
                        "environment": "unknown",
                        "symptom": "pod_not_ready",
                        "last_observed_at": NOW - timedelta(seconds=30),
                        "incident_id": None,
                    },
                ),
            }
        )
    )

    assert decision.action == "JOIN_GROUP"
    assert decision.selected_group_id == GROUP_1
    assert decision.reason_codes == ("same_problem_signature_window",)


def test_unknown_symptom_can_join_when_problem_signature_matches() -> None:
    decision = decide_alert_group(
        context(
            service=None,
            catalog_state=None,
            symptom="unknown",
            problem_key="f" * 64,
            candidates=(
                candidate(
                    service=None,
                    symptom="unknown",
                    problem_key="f" * 64,
                ),
            ),
        )
    )

    assert decision.action == "JOIN_GROUP"
    assert decision.reason_codes == ("same_problem_signature_window",)


@pytest.mark.parametrize(
    ("facts", "resource_type", "resource_name"),
    [
        (
            {
                "pod": "payment-7d9f",
                "instance": "10.0.0.1:8080",
                "node": "node-a",
                "container": "api",
            },
            "pod",
            "payment-7d9f",
        ),
        ({"instance": "payment-1", "node": "node-a"}, "instance", "payment-1"),
        ({"node": "node-a", "container": "api"}, "node", "node-a"),
        ({"container": "api"}, "container", "api"),
        ({}, "alert", ALERT_ID),
    ],
)
def test_resource_identity_uses_fixed_priority(
    facts: dict[str, str], resource_type: str, resource_name: str
) -> None:
    identity = derive_resource_identity(facts, ALERT_ID)

    assert identity.resource_type == resource_type
    assert identity.resource_name == resource_name
    assert len(identity.resource_key) == 64


def test_resource_summary_does_not_copy_forbidden_or_unsafe_values() -> None:
    identity = derive_resource_identity(
        {
            "scenario_id": "scenario-secret",
            "experiment_id": "experiment-secret",
            "token": "token-secret",
            "source_uri": "https://user:password@example.invalid/path",
            "pod": "https://user:password@example.invalid/path",
        },
        ALERT_ID,
    )

    serialized = identity.model_dump_json()
    for forbidden in ("scenario", "experiment", "token", "password", "https"):
        assert forbidden not in serialized.lower()
    assert identity.resource_type == "pod"
    assert identity.resource_name.startswith("pod-")


def test_context_and_candidate_inputs_are_bounded() -> None:
    with pytest.raises(ValidationError):
        context(candidates=tuple(candidate(id=f"agr_{index:032x}") for index in range(22)))
    with pytest.raises(ValidationError):
        context(symptom="x" * 65)
    with pytest.raises(ValidationError):
        candidate(id="group-1")
