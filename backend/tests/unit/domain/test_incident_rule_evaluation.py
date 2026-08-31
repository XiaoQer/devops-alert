from __future__ import annotations

from datetime import UTC, datetime, timedelta

from incident_intelligence.domain.incident_rule_evaluation import (
    AlertEvaluationFact,
    evaluate_rule,
)
from incident_intelligence.domain.incident_rules import IncidentRuleConfig

START = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)


def _config(**overrides: object) -> IncidentRuleConfig:
    values: dict[str, object] = {
        "environment": "production",
        "alert_source_ids": (),
        "services": (),
        "group_by": "SERVICE",
        "window_minutes": 5,
        "conditions": (
            {"type": "DISTINCT_ALERT_NAMES_GTE", "threshold": 2},
            {"type": "MAX_SEVERITY_AT_LEAST", "severity": "high"},
        ),
    }
    values.update(overrides)
    return IncidentRuleConfig.model_validate(values)


def _fact(
    alert_id: str,
    *,
    service: str | None = "checkout",
    alert_name: str = "HighLatency",
    severity: str = "medium",
    state: str = "ACTIVE",
    environment: str = "production",
    source_id: str = "src_11111111111111111111111111111111",
    entity_key: str | None = None,
    minute: int = 0,
) -> AlertEvaluationFact:
    suffix = alert_id.removeprefix("alt_")[-1:] or "a"
    return AlertEvaluationFact.model_validate(
        {
            "id": alert_id,
            "alert_source_id": source_id,
            "alert_name": alert_name,
            "state": state,
            "severity": severity,
            "environment": environment,
            "service": service,
            "entity_key": entity_key or suffix * 64,
            "entity_display_name": service or f"pod-{suffix}",
            "first_received_at": START + timedelta(minutes=minute),
        }
    )


def test_evaluator_groups_by_service_and_applies_all_conditions() -> None:
    result = evaluate_rule(
        _config(),
        (
            _fact("alt_a", alert_name="HighLatency", severity="medium", minute=0),
            _fact("alt_b", alert_name="ErrorRate", severity="high", minute=3),
            _fact(
                "alt_c",
                service="catalog",
                alert_name="ErrorRate",
                severity="critical",
                minute=3,
            ),
        ),
    )

    assert result.scanned_alert_count == 3
    assert result.truncated is False
    assert len(result.matches) == 1
    match = result.matches[0]
    assert match.group_display_name == "checkout"
    assert match.alert_count == 2
    assert match.distinct_alert_names == 2
    assert match.highest_severity == "high"
    assert match.example_alert_ids == ("alt_a", "alt_b")


def test_service_rule_excludes_alert_without_service() -> None:
    result = evaluate_rule(
        _config(
            conditions=({"type": "DISTINCT_ALERT_NAMES_GTE", "threshold": 1},),
        ),
        (_fact("alt_a", service=None),),
    )

    assert result.matches == ()


def test_entity_rule_groups_missing_service_by_entity_key() -> None:
    entity_key = "e" * 64
    result = evaluate_rule(
        _config(
            group_by="ENTITY",
            conditions=({"type": "ACTIVE_ALERTS_GTE", "threshold": 2},),
        ),
        (
            _fact("alt_a", service=None, entity_key=entity_key, minute=0),
            _fact("alt_b", service=None, entity_key=entity_key, minute=2),
        ),
    )

    assert len(result.matches) == 1
    assert result.matches[0].group_key == entity_key
    assert result.matches[0].active_alert_count == 2


def test_filters_environment_source_and_service_before_grouping() -> None:
    source_id = "src_22222222222222222222222222222222"
    result = evaluate_rule(
        _config(
            alert_source_ids=(source_id,),
            services=("checkout",),
            conditions=({"type": "ACTIVE_ALERTS_GTE", "threshold": 1},),
        ),
        (
            _fact("alt_a", source_id=source_id),
            _fact("alt_b", source_id="src_33333333333333333333333333333333"),
            _fact("alt_c", source_id=source_id, service="catalog"),
            _fact("alt_d", source_id=source_id, environment="staging"),
        ),
    )

    assert len(result.matches) == 1
    assert result.matches[0].example_alert_ids == ("alt_a",)


def test_window_excludes_alert_at_or_before_lower_boundary() -> None:
    result = evaluate_rule(
        _config(),
        (
            _fact("alt_a", alert_name="HighLatency", severity="high", minute=0),
            _fact("alt_b", alert_name="ErrorRate", severity="high", minute=6),
        ),
    )

    assert result.matches == ()


def test_all_conditions_use_and_semantics() -> None:
    result = evaluate_rule(
        _config(
            conditions=(
                {"type": "ACTIVE_ALERTS_GTE", "threshold": 2},
                {"type": "DISTINCT_ENTITIES_GTE", "threshold": 2},
            ),
        ),
        (
            _fact("alt_a", state="ACTIVE", entity_key="a" * 64),
            _fact("alt_b", state="RESOLVED", entity_key="b" * 64, minute=1),
        ),
    )

    assert result.matches == ()


def test_evaluator_merges_consecutive_windows_with_same_members() -> None:
    result = evaluate_rule(
        _config(),
        (
            _fact("alt_a", alert_name="HighLatency", severity="high", minute=0),
            _fact("alt_b", alert_name="ErrorRate", severity="high", minute=2),
            _fact("alt_b", alert_name="ErrorRate", severity="high", minute=2),
        ),
    )

    assert len(result.matches) == 1
    assert result.matches[0].example_alert_ids == ("alt_a", "alt_b")


def test_examples_are_bounded_to_ten_alerts() -> None:
    facts = tuple(_fact(f"alt_{index:02x}", minute=index) for index in range(11))
    result = evaluate_rule(
        _config(
            window_minutes=60,
            conditions=({"type": "ACTIVE_ALERTS_GTE", "threshold": 11},),
        ),
        facts,
    )

    assert result.matches[0].alert_count == 11
    assert len(result.matches[0].example_alert_ids) == 10


def test_evaluator_marks_result_truncated_at_match_limit() -> None:
    facts = tuple(
        _fact(
            f"alt_{index:02x}",
            service=f"service-{index}",
            minute=index,
        )
        for index in range(3)
    )
    result = evaluate_rule(
        _config(conditions=({"type": "ACTIVE_ALERTS_GTE", "threshold": 1},)),
        facts,
        max_matches=2,
    )

    assert len(result.matches) == 2
    assert result.truncated is True
