from datetime import UTC, datetime, timedelta

import pytest

from incident_intelligence.domain.alert_event_profiles import (
    ConfirmedEventMember,
    build_event_profile,
)
from incident_intelligence.domain.alert_text_similarity import normalize_alert_text

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
GROUP_ID = "agr_" + "1" * 32


def member(number: int, **overrides: object) -> ConfirmedEventMember:
    values: dict[str, object] = {
        "alert_id": f"alt_{number:032x}",
        "membership_state": "AUTO_CONFIRMED",
        "environment": "production",
        "service": "payment-api",
        "entity_key": f"{number:064x}",
        "scope_type": "SERVICE",
        "problem_key": "a" * 64,
        "problem_type": "PaymentHighErrorRate",
        "symptom": "errors",
        "topology_nodes": ("payment-api",),
        "normalized_text": normalize_alert_text(
            "支付错误率升高", f"HTTP 5xx 超过阈值 {number}", "PaymentHighErrorRate", "errors"
        ),
        "observed_at": NOW + timedelta(seconds=number),
    }
    values.update(overrides)
    return ConfirmedEventMember.model_validate(values)


def test_profile_contains_only_bounded_confirmed_facts_and_counts() -> None:
    profile = build_event_profile(
        GROUP_ID,
        (
            member(1),
            member(2, membership_state="MANUAL_CONFIRMED", entity_key="f" * 64),
        ),
    )

    assert profile.environment == "production"
    assert profile.member_ids == ("alt_" + f"{1:032x}", "alt_" + f"{2:032x}")
    assert profile.services == ("payment-api",)
    assert profile.entity_keys == (f"{1:064x}", "f" * 64)
    assert profile.auto_confirmed_count == 1
    assert profile.manual_confirmed_count == 1
    assert profile.first_observed_at == NOW + timedelta(seconds=1)
    assert profile.last_observed_at == NOW + timedelta(seconds=2)
    assert profile.core_services == ("payment-api",)
    assert profile.core_member_ids == profile.member_ids


def test_profile_text_center_is_deterministic_when_members_are_reordered() -> None:
    members = (
        member(1),
        member(
            2, normalized_text=normalize_alert_text("数据库锁等待", "事务阻塞", "LockWait", "lock")
        ),
        member(3),
    )

    first = build_event_profile(GROUP_ID, members)
    second = build_event_profile(GROUP_ID, tuple(reversed(members)))

    assert first.normalized_text == second.normalized_text
    assert first.member_ids == second.member_ids


def test_mixed_environment_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="event_profile_environment_mismatch"):
        build_event_profile(GROUP_ID, (member(1), member(2, environment="staging")))


def test_empty_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="event_profile_requires_confirmed_member"):
        build_event_profile(GROUP_ID, ())


def test_profile_core_excludes_single_edge_anchor() -> None:
    profile = build_event_profile(
        GROUP_ID,
        (
            member(1),
            member(2),
            member(
                3,
                service="edge-api",
                problem_key="b" * 64,
                problem_type="EdgeTimeout",
                symptom="latency",
                topology_nodes=("edge-api",),
            ),
        ),
    )

    assert "payment-api" in profile.core_services
    assert "edge-api" not in profile.core_services
    assert "alt_" + f"{3:032x}" not in profile.core_member_ids
