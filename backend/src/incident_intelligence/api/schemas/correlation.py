from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.domain.enums import CorrelationJobState, CorrelationOutcome
from incident_intelligence.domain.models import Environment, Severity


class CorrelationJobResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    alert_version: int
    state: CorrelationJobState
    attempts: int
    available_at: datetime
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


class CorrelationIncidentResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    state: str
    severity: Severity
    service: str
    environment: Environment
    detected_at: datetime
    version: int


class CorrelationDecisionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    outcome: CorrelationOutcome
    rule_version: str
    reason_codes: tuple[str, ...] = Field(max_length=10)
    facts: dict[str, object]
    candidate_incident_ids: tuple[str, ...] = Field(max_length=20)
    explanation: str
    created_at: datetime


class AlertCorrelationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    alert_id: str
    job: CorrelationJobResponse
    incident: CorrelationIncidentResponse | None
    decision: CorrelationDecisionResponse | None


class CorrelationJobListResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[CorrelationJobResponse, ...]
    limit: int
    offset: int
