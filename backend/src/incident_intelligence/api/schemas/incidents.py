from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
    created_at: datetime
    version: int
    alerts: tuple[IncidentAlertResponse, ...]
    alerts_truncated: bool
    correlation: IncidentCorrelationResponse | None
    timeline: tuple[IncidentTimelineResponse, ...]


class IncidentClaimResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    id: str
    assignee: str
    claimed_at: datetime
    version: int
