from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from incident_intelligence.domain.enums import AlertState
from incident_intelligence.domain.models import Alert
from incident_intelligence.domain.signal_intake import (
    SignalCommand,
    decide_alert_projection,
)

TIME_1 = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)
TIME_2 = TIME_1 + timedelta(minutes=5)
TIME_3 = TIME_2 + timedelta(minutes=5)
TIME_4 = TIME_3 + timedelta(minutes=5)

ALERT_ID = "alt_" + "a" * 32
NEW_ALERT_ID = "alt_" + "b" * 32
CURRENT_SIGNAL_ID = "sig_" + "c" * 32
NEW_SIGNAL_ID = "sig_" + "d" * 32
ALERT_SOURCE_ID = "src_" + "1" * 32


def firing_command(**overrides: object) -> SignalCommand:
    values: dict[str, object] = {
        "alert_source_id": ALERT_SOURCE_ID,
        "source": "alertmanager",
        "source_instance": "1" * 64,
        "source_event_id": "2" * 64,
        "source_alert_key": "payment-high-error-rate",
        "event_type": "alert.firing",
        "event_at": TIME_3,
        "episode_started_at": TIME_1,
        "title": "支付接口错误率升高",
        "summary": "支付接口错误率超过阈值",
        "severity": "high",
        "service": "payment-api",
        "environment": "production",
        "facts": {"region": "cn-east-1"},
        "normalization_reason_codes": (),
    }
    values.update(overrides)
    return SignalCommand.model_validate(values)


def resolved_command(**overrides: object) -> SignalCommand:
    values: dict[str, object] = {
        **firing_command().model_dump(),
        "source_event_id": "3" * 64,
        "event_type": "alert.resolved",
    }
    values.update(overrides)
    return SignalCommand.model_validate(values)


def current_alert(state: AlertState = AlertState.ACTIVE, **overrides: object) -> Alert:
    values: dict[str, object] = {
        "id": ALERT_ID,
        "signal_event_id": CURRENT_SIGNAL_ID,
        "alert_source_id": ALERT_SOURCE_ID,
        "source": "alertmanager",
        "source_instance": "1" * 64,
        "source_alert_key": "payment-high-error-rate",
        "state": state,
        "title": "旧标题",
        "severity": "medium",
        "service": "payment-api",
        "environment": "production",
        "first_observed_at": TIME_1,
        "last_observed_at": TIME_2,
        "state_changed_at": TIME_1 if state is AlertState.ACTIVE else TIME_2,
        "created_at": TIME_1,
        "version": 2 if state is AlertState.ACTIVE else 3,
    }
    values.update(overrides)
    return Alert.model_validate(values)


def test_first_firing_opens_active_alert() -> None:
    command = firing_command()

    decision = decide_alert_projection(None, command, NEW_ALERT_ID, NEW_SIGNAL_ID, TIME_4)

    assert decision.outcome == "opened"
    assert decision.reason_code == "first_firing_opened"
    assert decision.changes_projection is True
    assert decision.alert is not None
    assert decision.alert.id == NEW_ALERT_ID
    assert decision.alert.alert_source_id == ALERT_SOURCE_ID
    assert decision.alert.signal_event_id == NEW_SIGNAL_ID
    assert decision.alert.state is AlertState.ACTIVE
    assert decision.alert.first_observed_at == TIME_1
    assert decision.alert.last_observed_at == TIME_3
    assert decision.alert.state_changed_at == TIME_1
    assert decision.alert.created_at == TIME_4
    assert decision.alert.cycle == 1
    assert decision.alert.version == 1


def test_resolved_without_existing_alert_is_ignored() -> None:
    decision = decide_alert_projection(
        None, resolved_command(), NEW_ALERT_ID, NEW_SIGNAL_ID, TIME_4
    )

    assert decision.alert is None
    assert decision.outcome == "orphan_resolved"
    assert decision.reason_code == "orphan_resolved_ignored"
    assert decision.changes_projection is False


def test_active_firing_refreshes_content_and_increments_version() -> None:
    current = current_alert()
    command = firing_command(title="新标题", severity="critical")

    decision = decide_alert_projection(current, command, NEW_ALERT_ID, NEW_SIGNAL_ID, TIME_4)

    assert decision.outcome == "updated"
    assert decision.reason_code == "active_firing_updated"
    assert decision.alert is not None
    assert decision.alert.id == current.id
    assert decision.alert.signal_event_id == NEW_SIGNAL_ID
    assert decision.alert.title == "新标题"
    assert decision.alert.severity == "critical"
    assert decision.alert.last_observed_at == TIME_3
    assert decision.alert.state_changed_at == TIME_1
    assert decision.alert.created_at == current.created_at
    assert decision.alert.cycle == current.cycle
    assert decision.alert.version == 3


def test_active_alert_resolves_at_same_or_later_event_time() -> None:
    current = current_alert(last_observed_at=TIME_3)
    command = resolved_command(event_at=TIME_3)

    decision = decide_alert_projection(current, command, NEW_ALERT_ID, NEW_SIGNAL_ID, TIME_4)

    assert decision.outcome == "resolved"
    assert decision.reason_code == "active_alert_resolved"
    assert decision.alert is not None
    assert decision.alert.state is AlertState.RESOLVED
    assert decision.alert.signal_event_id == NEW_SIGNAL_ID
    assert decision.alert.last_observed_at == TIME_3
    assert decision.alert.state_changed_at == TIME_3
    assert decision.alert.cycle == current.cycle
    assert decision.alert.version == 3


def test_older_event_is_stale_and_does_not_replace_projection_signal() -> None:
    current = current_alert(last_observed_at=TIME_3)
    command = firing_command(event_at=TIME_2)

    decision = decide_alert_projection(current, command, NEW_ALERT_ID, NEW_SIGNAL_ID, TIME_4)

    assert decision.alert is current
    assert decision.alert.signal_event_id == CURRENT_SIGNAL_ID
    assert decision.outcome == "stale"
    assert decision.reason_code == "event_older_than_projection"
    assert decision.changes_projection is False


def test_resolved_alert_reopens_only_for_a_newer_episode() -> None:
    current = current_alert(AlertState.RESOLVED, state_changed_at=TIME_2, version=3)
    stale = firing_command(event_at=TIME_2, episode_started_at=TIME_2)
    newer = firing_command(event_at=TIME_3, episode_started_at=TIME_3)

    stale_decision = decide_alert_projection(current, stale, NEW_ALERT_ID, NEW_SIGNAL_ID, TIME_4)
    reopen_decision = decide_alert_projection(current, newer, NEW_ALERT_ID, NEW_SIGNAL_ID, TIME_4)

    assert stale_decision.outcome == "stale"
    assert stale_decision.reason_code == "firing_episode_not_newer"
    assert stale_decision.alert is current
    assert reopen_decision.outcome == "reopened"
    assert reopen_decision.reason_code == "new_episode_reopened"
    assert reopen_decision.alert is not None
    assert reopen_decision.alert.state is AlertState.ACTIVE
    assert reopen_decision.alert.first_observed_at == TIME_3
    assert reopen_decision.alert.state_changed_at == TIME_3
    assert reopen_decision.alert.cycle == current.cycle + 1
    assert reopen_decision.alert.version == 4


def test_resolved_wins_when_firing_arrives_at_the_same_event_time() -> None:
    active = current_alert(last_observed_at=TIME_2)
    resolution = decide_alert_projection(
        active,
        resolved_command(event_at=TIME_2),
        NEW_ALERT_ID,
        NEW_SIGNAL_ID,
        TIME_4,
    )
    assert resolution.alert is not None

    firing = decide_alert_projection(
        resolution.alert,
        firing_command(event_at=TIME_2, episode_started_at=TIME_2),
        NEW_ALERT_ID,
        "sig_" + "e" * 32,
        TIME_4,
    )

    assert firing.outcome == "stale"
    assert firing.alert is resolution.alert
    assert firing.alert.state is AlertState.RESOLVED


def test_same_time_active_firing_uses_last_received_normalized_content() -> None:
    current = current_alert(last_observed_at=TIME_2)
    command = firing_command(event_at=TIME_2, title="最后收到的标题")

    decision = decide_alert_projection(current, command, NEW_ALERT_ID, NEW_SIGNAL_ID, TIME_4)

    assert decision.outcome == "updated"
    assert decision.alert is not None
    assert decision.alert.title == "最后收到的标题"
    assert decision.alert.version == 3


def test_projection_preserves_pod_identity_without_service() -> None:
    command = firing_command(
        service=None,
        entity_type="POD",
        entity_key="4" * 64,
        entity_display_name="devops-platform/demo-0",
    )

    decision = decide_alert_projection(None, command, NEW_ALERT_ID, NEW_SIGNAL_ID, TIME_4)

    assert decision.alert is not None
    assert decision.alert.service is None
    assert decision.alert.entity_type == "POD"
    assert decision.alert.entity_key == "4" * 64
    assert decision.alert.entity_display_name == "devops-platform/demo-0"


def test_already_resolved_alert_does_not_change_for_another_resolution() -> None:
    current = current_alert(AlertState.RESOLVED)

    decision = decide_alert_projection(
        current,
        resolved_command(event_at=TIME_3),
        NEW_ALERT_ID,
        NEW_SIGNAL_ID,
        TIME_4,
    )

    assert decision.outcome == "stale"
    assert decision.reason_code == "alert_already_resolved"
    assert decision.alert is current


def test_suppressed_alert_is_not_changed_by_external_projection() -> None:
    current = current_alert(AlertState.SUPPRESSED)

    decision = decide_alert_projection(
        current,
        firing_command(event_at=TIME_3),
        NEW_ALERT_ID,
        NEW_SIGNAL_ID,
        TIME_4,
    )

    assert decision.outcome == "stale"
    assert decision.reason_code == "alert_suppressed"
    assert decision.alert is current


def test_signal_command_is_immutable_and_rejects_unbounded_identity_and_facts() -> None:
    command = firing_command()

    with pytest.raises(ValidationError):
        command.title = "修改标题"

    with pytest.raises(ValidationError) as error:
        firing_command(
            source="manual",
            source_instance="short",
            facts={f"key-{index}": "value" for index in range(21)},
        )

    locations = {item["loc"] for item in error.value.errors()}
    assert ("source",) in locations
    assert ("source_instance",) in locations
    assert ("facts",) in locations

    with pytest.raises(ValidationError):
        firing_command(event_at=TIME_2, episode_started_at=TIME_3)
