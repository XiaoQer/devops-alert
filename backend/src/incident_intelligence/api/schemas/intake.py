from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from incident_intelligence.domain.signal_intake import ProjectionOutcome
from incident_intelligence.services.signal_intake import SignalIntakeBatchResult


class IntakeItemResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_event_id: str
    alert_id: str | None
    outcome: ProjectionOutcome
    replayed: bool


class IntakeCountsResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    opened: int
    updated: int
    resolved: int
    reopened: int
    stale: int
    orphan_resolved: int
    replayed: int


class IntakeBatchResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[IntakeItemResponse, ...]
    counts: IntakeCountsResponse

    @classmethod
    def from_result(cls, result: SignalIntakeBatchResult) -> IntakeBatchResponse:
        return cls.model_validate(result.model_dump())
