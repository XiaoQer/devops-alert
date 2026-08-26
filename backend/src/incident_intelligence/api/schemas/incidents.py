from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.domain.enums import IncidentState
from incident_intelligence.domain.incident_operations import (
    IncidentNoteCategory,
    IncidentResolutionCategory,
)


class IncidentListItemResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    id: str
    title: str
    severity: str
    state: str
    service: str
    environment: str
    assignee: str | None
    owner_team: str | None
    detected_at: datetime
    last_activity_at: datetime
    alert_count: int
    version: int


class IncidentListResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[IncidentListItemResponse, ...]
    total: int
    limit: int
    offset: int


class IncidentAlertResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    id: str
    title: str
    state: str
    severity: str
    source: str
    first_observed_at: datetime
    last_observed_at: datetime
    version: int


class IncidentCorrelationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    outcome: str
    rule_version: str
    reason_codes: tuple[str, ...] = Field(max_length=10)
    explanation: str
    created_at: datetime


class IncidentTimelineResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    id: str
    kind: str
    occurred_at: datetime
    title: str
    detail: str


class IncidentActivityResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    id: str
    kind: str
    actor: str
    from_state: str | None
    to_state: str | None
    note_category: str | None
    message: str | None
    resolution_category: str | None
    resolution_actions: str | None
    root_cause: str | None
    incident_version: int
    created_at: datetime


class IncidentPrimaryActionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    action: str
    target_state: str | None


class IncidentOverviewResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    id: str
    title: str
    severity: str
    state: str
    service: str
    environment: str
    assignee: str | None
    claimed_at: datetime | None
    owner_team: str | None
    detected_at: datetime
    state_changed_at: datetime
    resolved_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    version: int
    alerts: tuple[IncidentAlertResponse, ...]
    alerts_truncated: bool
    correlation: IncidentCorrelationResponse | None
    activities: tuple[IncidentActivityResponse, ...]
    activities_truncated: bool
    allowed_actions: tuple[str, ...]
    allowed_transitions: tuple[str, ...]
    primary_action: IncidentPrimaryActionResponse | None
    timeline: tuple[IncidentTimelineResponse, ...]


class IncidentOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class IncidentClaimRequest(IncidentOperationRequest):
    pass


class IncidentReleaseRequest(IncidentOperationRequest):
    pass


class IncidentTransitionRequest(IncidentOperationRequest):
    target_state: IncidentState
    message: str = Field(min_length=1, max_length=1_000)


class IncidentNoteRequest(IncidentOperationRequest):
    category: IncidentNoteCategory
    message: str = Field(min_length=1, max_length=2_000)


class IncidentResolveRequest(IncidentOperationRequest):
    category: IncidentResolutionCategory
    message: str = Field(min_length=1, max_length=2_000)
    resolution_actions: str = Field(min_length=1, max_length=4_000)
    root_cause: str | None = Field(default=None, max_length=4_000)


class IncidentReopenRequest(IncidentOperationRequest):
    reason: str = Field(min_length=1, max_length=2_000)


class IncidentCloseRequest(IncidentOperationRequest):
    message: str = Field(min_length=1, max_length=2_000)


class IncidentOperationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    id: str
    action: str
    state: str
    assignee: str | None
    version: int
    activity_id: str
    occurred_at: datetime
