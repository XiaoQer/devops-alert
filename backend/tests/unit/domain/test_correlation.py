# ruff: noqa: RUF001

import pytest
from pydantic import ValidationError

from incident_intelligence.domain.correlation import (
    CorrelationContext,
    decide_correlation,
)

INCIDENT_1 = "inc_" + "1" * 32
INCIDENT_2 = "inc_" + "2" * 32
INCIDENT_3 = "inc_" + "3" * 32


def eligible_context(**overrides: object) -> CorrelationContext:
    values: dict[str, object] = {
        "alert_version": 2,
        "current_alert_version": 2,
        "alert_state": "ACTIVE",
        "severity": "high",
        "environment": "production",
        "catalog_state": "ACTIVE",
        "existing_incident_id": None,
        "exact_candidate_ids": (),
        "dependency_candidate_ids": (),
    }
    values.update(overrides)
    return CorrelationContext.model_validate(values)


def test_superseded_alert_version_stops_before_existing_link_and_eligibility() -> None:
    decision = decide_correlation(
        eligible_context(
            alert_version=1,
            current_alert_version=2,
            alert_state="RESOLVED",
            existing_incident_id=INCIDENT_1,
        )
    )

    assert decision.outcome == "SUPERSEDED"
    assert decision.action == "NONE"
    assert decision.selected_incident_id is None
    assert decision.candidate_incident_ids == ()
    assert decision.reason_codes == ("alert_version_superseded",)
    assert decision.explanation == "该任务对应的告警版本已被新版本取代。"


def test_active_alert_with_existing_link_keeps_the_existing_incident() -> None:
    decision = decide_correlation(eligible_context(existing_incident_id=INCIDENT_1, severity="low"))

    assert decision.outcome == "LINKED_EXISTING"
    assert decision.action == "NONE"
    assert decision.selected_incident_id == INCIDENT_1
    assert decision.reason_codes == ("alert_already_linked",)
    assert decision.explanation == "告警已经属于现有事故，保持原关联。"


def test_resolved_alert_with_existing_link_records_without_closing_incident() -> None:
    decision = decide_correlation(
        eligible_context(alert_state="RESOLVED", existing_incident_id=INCIDENT_1)
    )

    assert decision.outcome == "RECORDED_RESOLUTION"
    assert decision.action == "NONE"
    assert decision.selected_incident_id == INCIDENT_1
    assert decision.reason_codes == ("alert_resolved_no_incident_close",)
    assert decision.explanation == "告警已经恢复，但事故不会自动关闭。"


@pytest.mark.parametrize(
    ("overrides", "reason", "explanation"),
    [
        (
            {"alert_state": "RESOLVED"},
            "alert_not_active",
            "告警当前不是活动状态，未进入事故关联。",
        ),
        (
            {"severity": "medium"},
            "severity_below_threshold",
            "告警严重度未达到 critical/high，未创建事故。",
        ),
        (
            {"environment": "staging"},
            "non_production_environment",
            "告警不属于生产环境，未创建事故。",
        ),
        (
            {"catalog_state": None},
            "service_not_registered",
            "服务尚未登记，未创建事故。",
        ),
        (
            {"catalog_state": "INACTIVE"},
            "service_inactive",
            "服务目录项已停用，未创建事故。",
        ),
    ],
)
def test_ineligible_alerts_are_rejected_with_one_fixed_reason(
    overrides: dict[str, object],
    reason: str,
    explanation: str,
) -> None:
    decision = decide_correlation(eligible_context(**overrides))

    assert decision.outcome == "REJECTED_INELIGIBLE"
    assert decision.action == "NONE"
    assert decision.selected_incident_id is None
    assert decision.candidate_incident_ids == ()
    assert decision.reason_codes == (reason,)
    assert decision.explanation == explanation


def test_no_candidate_creates_an_independent_incident() -> None:
    decision = decide_correlation(eligible_context())

    assert decision.outcome == "CREATED_NO_MATCH"
    assert decision.action == "CREATE"
    assert decision.selected_incident_id is None
    assert decision.candidate_incident_ids == ()
    assert decision.reason_codes == ("no_same_service_candidate",)
    assert decision.explanation == "窗口内没有同服务事故，已创建独立事故。"


def test_unique_exact_service_candidate_is_the_only_auto_link_case() -> None:
    decision = decide_correlation(eligible_context(exact_candidate_ids=(INCIDENT_1,)))

    assert decision.outcome == "LINKED_EXACT_SERVICE"
    assert decision.action == "LINK"
    assert decision.selected_incident_id == INCIDENT_1
    assert decision.candidate_incident_ids == (INCIDENT_1,)
    assert decision.reason_codes == ("one_exact_service_candidate",)
    assert decision.explanation == "窗口内只有一个同服务事故，已自动关联。"


def test_multiple_exact_service_candidates_create_an_ambiguous_incident() -> None:
    decision = decide_correlation(eligible_context(exact_candidate_ids=(INCIDENT_1, INCIDENT_2)))

    assert decision.outcome == "CREATED_AMBIGUOUS"
    assert decision.action == "CREATE"
    assert decision.selected_incident_id is None
    assert decision.candidate_incident_ids == (INCIDENT_1, INCIDENT_2)
    assert decision.reason_codes == ("multiple_exact_service_candidates",)
    assert decision.explanation == "窗口内存在多个同服务事故，为避免误合并已创建独立事故。"


def test_dependency_candidate_is_only_a_hint_and_creates_independent_incident() -> None:
    decision = decide_correlation(
        eligible_context(dependency_candidate_ids=(INCIDENT_2, INCIDENT_3))
    )

    assert decision.outcome == "CREATED_DEPENDENCY_CANDIDATE"
    assert decision.action == "CREATE"
    assert decision.selected_incident_id is None
    assert decision.candidate_incident_ids == (INCIDENT_2, INCIDENT_3)
    assert decision.reason_codes == ("dependency_symptom_candidate",)
    assert decision.explanation == "发现一跳服务的同症状事故，仅标记可能相关。"


def test_more_than_twenty_candidates_never_auto_link_and_are_bounded() -> None:
    candidates = tuple(f"inc_{index:032x}" for index in range(21))

    decision = decide_correlation(eligible_context(exact_candidate_ids=candidates))

    assert decision.outcome == "CREATED_AMBIGUOUS"
    assert decision.action == "CREATE"
    assert decision.candidate_incident_ids == candidates[:20]
    assert decision.reason_codes == ("candidate_limit_reached",)
    assert decision.explanation == "候选数量超过安全上限，未执行自动合并。"


def test_correlation_context_rejects_invalid_versions_ids_and_extra_fields() -> None:
    for overrides in (
        {"alert_version": 0},
        {"current_alert_version": 0},
        {"existing_incident_id": "incident-1"},
        {"exact_candidate_ids": tuple(f"inc_{index:032x}" for index in range(22))},
        {"unexpected": "value"},
    ):
        with pytest.raises(ValidationError):
            eligible_context(**overrides)
