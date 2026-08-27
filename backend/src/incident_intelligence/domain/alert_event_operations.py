from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

AlertGroupId = Annotated[str, StringConstraints(pattern=r"^agr_[0-9a-f]{32}$")]
AlertId = Annotated[str, StringConstraints(pattern=r"^alt_[0-9a-f]{32}$")]
OperationReason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=2, max_length=500)
]


class ConfirmAlertEventMemberCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_version: int = Field(ge=1)
    reason: OperationReason


class SplitAlertEventMembersCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_version: int = Field(ge=1)
    alert_ids: tuple[AlertId, ...] = Field(min_length=1, max_length=100)
    reason: OperationReason

    @field_validator("alert_ids")
    @classmethod
    def require_unique_alert_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("alert_ids 不能重复")
        return value


class MergeAlertEventsCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_version: int = Field(ge=1)
    source_group_id: AlertGroupId
    reason: OperationReason


class AlertEventOperationRuleError(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def require_split_allowed(*, total_count: int, selected_count: int) -> None:
    if selected_count < 1 or selected_count >= total_count:
        raise AlertEventOperationRuleError("alert_event_split_all_members")


def require_merge_allowed(
    *,
    target_group_id: str,
    source_group_id: str,
    target_environment: str,
    source_environment: str,
    combined_member_count: int,
    member_limit: int,
    target_incident_id: str | None,
    source_incident_id: str | None,
) -> None:
    if target_group_id == source_group_id:
        raise AlertEventOperationRuleError("alert_event_same_group")
    if target_environment != source_environment:
        raise AlertEventOperationRuleError("alert_event_environment_conflict")
    if combined_member_count > member_limit:
        raise AlertEventOperationRuleError("alert_event_member_limit_exceeded")
    if (
        target_incident_id is not None
        and source_incident_id is not None
        and target_incident_id != source_incident_id
    ):
        raise AlertEventOperationRuleError("alert_event_incident_conflict")
