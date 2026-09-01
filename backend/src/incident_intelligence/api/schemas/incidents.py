from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.services.incidents import (
    IncidentDetailView,
    IncidentMutationResult,
    IncidentPageView,
)


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IncidentVersionRequest(_Request):
    expected_version: int = Field(ge=1)


class ResolveIncidentRequest(IncidentVersionRequest):
    resolution_summary: str = Field(min_length=1, max_length=2_000)


class IncidentPageResponse(IncidentPageView):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


class IncidentDetailResponse(IncidentDetailView):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


class IncidentMutationResponse(IncidentMutationResult):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


__all__ = [
    "IncidentDetailResponse",
    "IncidentMutationResponse",
    "IncidentPageResponse",
    "IncidentVersionRequest",
    "ResolveIncidentRequest",
]
