import pytest
from pydantic import ValidationError

from incident_intelligence.domain.alert_event_operations import (
    AlertEventOperationRuleError,
    MergeAlertEventsCommand,
    SplitAlertEventMembersCommand,
    require_merge_allowed,
    require_split_allowed,
)

GROUP_A = "agr_" + "1" * 32
GROUP_B = "agr_" + "2" * 32
ALERT_A = "alt_" + "1" * 32
ALERT_B = "alt_" + "2" * 32


def test_split_command_rejects_duplicate_alert_ids() -> None:
    with pytest.raises(ValidationError):
        SplitAlertEventMembersCommand(
            expected_version=1,
            alert_ids=(ALERT_A, ALERT_A),
            reason="需要拆出误归组成员",
        )


def test_split_cannot_remove_every_member_from_source_event() -> None:
    with pytest.raises(AlertEventOperationRuleError) as captured:
        require_split_allowed(total_count=2, selected_count=2)

    assert captured.value.reason_code == "alert_event_split_all_members"


@pytest.mark.parametrize(
    ("overrides", "reason_code"),
    (
        ({"source_group_id": GROUP_A}, "alert_event_same_group"),
        ({"source_environment": "development"}, "alert_event_environment_conflict"),
        ({"combined_member_count": 1_001}, "alert_event_member_limit_exceeded"),
        (
            {
                "source_incident_id": "inc_" + "1" * 32,
                "target_incident_id": "inc_" + "2" * 32,
            },
            "alert_event_incident_conflict",
        ),
    ),
)
def test_merge_rejects_unsafe_event_relationships(
    overrides: dict[str, object], reason_code: str
) -> None:
    values: dict[str, object] = {
        "target_group_id": GROUP_A,
        "source_group_id": GROUP_B,
        "target_environment": "production",
        "source_environment": "production",
        "combined_member_count": 2,
        "member_limit": 1_000,
        "target_incident_id": None,
        "source_incident_id": None,
    }
    values.update(overrides)

    with pytest.raises(AlertEventOperationRuleError) as captured:
        require_merge_allowed(**values)  # type: ignore[arg-type]

    assert captured.value.reason_code == reason_code


def test_merge_command_normalizes_reason_without_accepting_unknown_fields() -> None:
    command = MergeAlertEventsCommand(
        expected_version=2,
        source_group_id=GROUP_B,
        reason="  同一故障传播链  ",
    )
    assert command.reason == "同一故障传播链"

    with pytest.raises(ValidationError):
        MergeAlertEventsCommand(
            expected_version=2,
            source_group_id=GROUP_B,
            reason="同一故障传播链",
            scenario_id="forbidden",
        )
