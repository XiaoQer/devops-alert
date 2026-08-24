from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ResourceResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    id: str
    created_at: datetime
    version: int


class SignalEventResponse(ResourceResponse):
    source: str
    event_type: Literal["manual.reported", "alert.firing", "alert.resolved"]
    title: str
    summary: str
    severity: Literal["critical", "high", "medium", "low"]
    service: str
    environment: Literal["production", "staging", "development", "unknown"]
    observed_at: datetime
    received_at: datetime
    facts: dict[str, str]


class AlertResponse(ResourceResponse):
    signal_event_id: str
    source: str
    source_instance: str
    source_alert_key: str
    state: Literal["ACTIVE", "RESOLVED", "SUPPRESSED"]
    title: str
    severity: Literal["critical", "high", "medium", "low"]
    service: str
    environment: Literal["production", "staging", "development", "unknown"]
    first_observed_at: datetime
    last_observed_at: datetime
    state_changed_at: datetime


class IncidentResponse(ResourceResponse):
    primary_alert_id: str
    state: Literal[
        "DETECTED",
        "TRIAGING",
        "INVESTIGATING",
        "MITIGATING",
        "MONITORING_RECOVERY",
        "RESOLVED",
        "CLOSED",
    ]
    title: str
    severity: Literal["critical", "high", "medium", "low"]
    service: str
    environment: Literal["production", "staging", "development", "unknown"]
    detected_at: datetime


class DiagnosisRunResponse(ResourceResponse):
    incident_id: str
    incident_context_version: int
    state: Literal[
        "QUEUED",
        "COLLECTING",
        "NORMALIZING",
        "SNAPSHOT_READY",
        "ANALYZING",
        "REPORT_READY",
        "PARTIAL",
        "FAILED",
        "REVIEW_REQUIRED",
    ]
