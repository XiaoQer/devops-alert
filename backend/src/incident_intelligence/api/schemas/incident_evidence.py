from pydantic import ConfigDict

from incident_intelligence.services.incident_evidence import (
    EvidenceRunDetail,
    EvidenceRunMutationResult,
    EvidenceRunPage,
)


class EvidenceRunPageResponse(EvidenceRunPage):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


class EvidenceRunDetailResponse(EvidenceRunDetail):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


class EvidenceRunMutationResponse(EvidenceRunMutationResult):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


__all__ = [
    "EvidenceRunDetailResponse",
    "EvidenceRunMutationResponse",
    "EvidenceRunPageResponse",
]
