from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from incident_intelligence.domain.enums import AlertState
from incident_intelligence.domain.models import (
    Alert,
    Environment,
    FactKey,
    FactValue,
    ServiceName,
    Severity,
    Summary,
    Title,
    UtcAwareDatetime,
)

ProjectionOutcome = Literal["opened", "updated", "resolved", "reopened", "stale", "orphan_resolved"]
NormalizationReasonCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]


class SignalCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    alert_source_id: str = Field(pattern=r"^src_[0-9a-f]{32}$")
    source: Literal["alertmanager", "cloudevents"]
    source_instance: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_event_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_alert_key: str = Field(min_length=1, max_length=128)
    event_type: Literal["alert.firing", "alert.resolved"]
    event_at: UtcAwareDatetime
    episode_started_at: UtcAwareDatetime
    title: Title
    summary: Summary
    severity: Severity
    service: ServiceName
    environment: Environment
    facts: dict[FactKey, FactValue] = Field(default_factory=dict, max_length=20)
    normalization_reason_codes: tuple[NormalizationReasonCode, ...] = Field(
        default=(), max_length=10
    )

    @model_validator(mode="after")
    def validate_episode_order(self) -> SignalCommand:
        if self.episode_started_at > self.event_at:
            raise ValueError("episode_started_at 不得晚于 event_at")
        return self


class ProjectionDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    alert: Alert | None
    outcome: ProjectionOutcome
    reason_code: NormalizationReasonCode
    changes_projection: bool


def decide_alert_projection(
    current: Alert | None,
    command: SignalCommand,
    new_alert_id: str,
    signal_event_id: str,
    now: datetime,
) -> ProjectionDecision:
    if current is None:
        if command.event_type == "alert.resolved":
            return _unchanged(None, "orphan_resolved", "orphan_resolved_ignored")
        return ProjectionDecision(
            alert=Alert(
                id=new_alert_id,
                signal_event_id=signal_event_id,
                alert_source_id=command.alert_source_id,
                source=command.source,
                source_instance=command.source_instance,
                source_alert_key=command.source_alert_key,
                state=AlertState.ACTIVE,
                title=command.title,
                severity=command.severity,
                service=command.service,
                environment=command.environment,
                first_observed_at=command.episode_started_at,
                last_observed_at=command.event_at,
                state_changed_at=command.episode_started_at,
                created_at=now,
            ),
            outcome="opened",
            reason_code="first_firing_opened",
            changes_projection=True,
        )

    if command.event_at < current.last_observed_at:
        return _unchanged(current, "stale", "event_older_than_projection")

    if current.state is AlertState.SUPPRESSED:
        return _unchanged(current, "stale", "alert_suppressed")

    if current.state is AlertState.RESOLVED:
        if command.event_type == "alert.resolved":
            return _unchanged(current, "stale", "alert_already_resolved")
        if command.episode_started_at <= current.state_changed_at:
            return _unchanged(current, "stale", "firing_episode_not_newer")
        return ProjectionDecision(
            alert=_project_command(
                current,
                command,
                signal_event_id,
                state=AlertState.ACTIVE,
                first_observed_at=command.episode_started_at,
                state_changed_at=command.episode_started_at,
            ),
            outcome="reopened",
            reason_code="new_episode_reopened",
            changes_projection=True,
        )

    if command.event_type == "alert.resolved":
        return ProjectionDecision(
            alert=_project_command(
                current,
                command,
                signal_event_id,
                state=AlertState.RESOLVED,
                state_changed_at=command.event_at,
            ),
            outcome="resolved",
            reason_code="active_alert_resolved",
            changes_projection=True,
        )

    return ProjectionDecision(
        alert=_project_command(current, command, signal_event_id, state=AlertState.ACTIVE),
        outcome="updated",
        reason_code="active_firing_updated",
        changes_projection=True,
    )


def _project_command(
    current: Alert,
    command: SignalCommand,
    signal_event_id: str,
    *,
    state: AlertState,
    first_observed_at: datetime | None = None,
    state_changed_at: datetime | None = None,
) -> Alert:
    return Alert.model_validate(
        {
            **current.model_dump(),
            "signal_event_id": signal_event_id,
            "state": state,
            "title": command.title,
            "severity": command.severity,
            "service": command.service,
            "environment": command.environment,
            "first_observed_at": first_observed_at or current.first_observed_at,
            "last_observed_at": command.event_at,
            "state_changed_at": state_changed_at or current.state_changed_at,
            "version": current.version + 1,
        }
    )


def _unchanged(
    alert: Alert | None,
    outcome: Literal["stale", "orphan_resolved"],
    reason_code: str,
) -> ProjectionDecision:
    return ProjectionDecision(
        alert=alert,
        outcome=outcome,
        reason_code=reason_code,
        changes_projection=False,
    )
