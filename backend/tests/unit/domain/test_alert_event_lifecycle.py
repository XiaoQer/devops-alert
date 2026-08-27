from datetime import UTC, datetime, timedelta

from incident_intelligence.domain.alert_event_lifecycle import (
    LifecycleContext,
    LifecyclePolicy,
    decide_lifecycle,
)

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
POLICY = LifecyclePolicy()


def test_forming_event_remains_visible_until_initial_window_expires() -> None:
    decision = decide_lifecycle(
        LifecycleContext(
            state="FORMING",
            active_count=1,
            now=NOW,
            forming_until=NOW + timedelta(seconds=10),
        ),
        POLICY,
    )

    assert decision.state == "FORMING"
    assert decision.schedule_at == NOW + timedelta(seconds=10)
    assert decision.reason_code == "forming_window_active"


def test_forming_event_with_active_member_becomes_active_when_window_expires() -> None:
    decision = decide_lifecycle(
        LifecycleContext(
            state="FORMING",
            active_count=1,
            now=NOW,
            forming_until=NOW,
        ),
        POLICY,
    )

    assert decision.state == "ACTIVE"
    assert decision.schedule_at is None
    assert decision.reason_code == "forming_window_completed"


def test_all_resolved_enters_observing_then_closes() -> None:
    observing = decide_lifecycle(
        LifecycleContext(state="ACTIVE", active_count=0, now=NOW),
        POLICY,
    )
    assert observing.state == "OBSERVING"
    assert observing.schedule_at == NOW + timedelta(minutes=5)
    assert observing.observing_until == NOW + timedelta(minutes=5)

    closed = decide_lifecycle(
        LifecycleContext(
            state="OBSERVING",
            active_count=0,
            now=NOW,
            observing_until=NOW,
        ),
        POLICY,
    )
    assert closed.state == "CLOSED"
    assert closed.schedule_at is None
    assert closed.closed_at == NOW


def test_new_active_member_during_observation_returns_event_to_active() -> None:
    decision = decide_lifecycle(
        LifecycleContext(
            state="OBSERVING",
            active_count=1,
            now=NOW,
            observing_until=NOW + timedelta(minutes=2),
        ),
        POLICY,
    )

    assert decision.state == "ACTIVE"
    assert decision.observing_until is None
    assert decision.reason_code == "activity_resumed_during_observation"


def test_closed_event_is_terminal_without_explicit_late_correction() -> None:
    decision = decide_lifecycle(
        LifecycleContext(state="CLOSED", active_count=1, now=NOW),
        POLICY,
    )

    assert decision.state == "CLOSED"
    assert decision.reason_code == "closed_event_is_terminal"


def test_accepted_late_member_can_correct_a_recently_closed_event() -> None:
    decision = decide_lifecycle(
        LifecycleContext(
            state="CLOSED",
            active_count=1,
            now=NOW,
            closed_at=NOW - timedelta(minutes=4),
            allow_late_correction=True,
        ),
        POLICY,
    )

    assert decision.state == "ACTIVE"
    assert decision.closed_at is None
    assert decision.reason_code == "late_member_corrected_closed_state"


def test_late_correction_outside_tolerance_keeps_event_closed() -> None:
    decision = decide_lifecycle(
        LifecycleContext(
            state="CLOSED",
            active_count=1,
            now=NOW,
            closed_at=NOW - timedelta(minutes=5, seconds=1),
            allow_late_correction=True,
        ),
        POLICY,
    )

    assert decision.state == "CLOSED"
    assert decision.reason_code == "late_correction_window_expired"


def test_policy_rejects_unbounded_member_limit() -> None:
    try:
        LifecyclePolicy(member_limit=1_001)
    except ValueError as error:
        assert "member_limit" in str(error)
    else:
        raise AssertionError("expected member limit validation failure")
