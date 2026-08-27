from incident_intelligence.domain.alert_group_correlation import (
    AlertGroupCorrelationContext,
    decide_alert_group_correlation,
)

INCIDENT_ID = "inc_" + "1" * 32


def context(**overrides: object) -> AlertGroupCorrelationContext:
    values: dict[str, object] = {
        "target_group_version": 2,
        "current_group_version": 2,
        "group_state": "ACTIVE",
        "severity": "high",
        "environment": "production",
        "service_present": True,
        "catalog_state": "ACTIVE",
        "existing_incident_id": None,
        "exact_candidate_ids": (),
        "dependency_candidate_ids": (),
    }
    values.update(overrides)
    return AlertGroupCorrelationContext.model_validate(values)


def test_current_group_without_candidate_creates_incident() -> None:
    decision = decide_alert_group_correlation(context())

    assert decision.action == "CREATE"
    assert decision.outcome == "CREATED_NO_MATCH"


def test_superseded_group_version_does_not_create_incident() -> None:
    decision = decide_alert_group_correlation(context(target_group_version=1))

    assert decision.action == "NONE"
    assert decision.outcome == "SUPERSEDED"


def test_existing_incident_is_kept_even_after_group_resolution() -> None:
    decision = decide_alert_group_correlation(
        context(group_state="CLOSED", existing_incident_id=INCIDENT_ID)
    )

    assert decision.action == "NONE"
    assert decision.outcome == "RECORDED_RESOLUTION"
    assert decision.selected_incident_id == INCIDENT_ID


def test_unique_incident_candidate_is_linked() -> None:
    decision = decide_alert_group_correlation(context(exact_candidate_ids=(INCIDENT_ID,)))

    assert decision.action == "LINK"
    assert decision.selected_incident_id == INCIDENT_ID


def test_non_production_group_is_not_correlated() -> None:
    decision = decide_alert_group_correlation(context(environment="staging"))

    assert decision.action == "NONE"
    assert decision.outcome == "REJECTED_INELIGIBLE"
    assert decision.reason_codes == ("non_production_environment",)


def test_group_without_service_is_explicitly_skipped() -> None:
    decision = decide_alert_group_correlation(context(service_present=False, catalog_state=None))

    assert decision.action == "NONE"
    assert decision.outcome == "SKIPPED_SERVICE_MISSING"
    assert decision.reason_codes == ("service_missing",)
    assert decision.explanation == "告警未提供服务标识，已保留告警组但不自动创建事故。"  # noqa: RUF001


def test_ambiguous_candidates_are_not_merged() -> None:
    decision = decide_alert_group_correlation(
        context(exact_candidate_ids=(INCIDENT_ID, "inc_" + "2" * 32))
    )

    assert decision.action == "CREATE"
    assert decision.outcome == "CREATED_AMBIGUOUS"
    assert decision.selected_incident_id is None
