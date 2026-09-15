from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.services.incident_diagnosis import (
    DiagnosisRunDetail,
    DiagnosisRunMutationResult,
    DiagnosisRunPage,
)


class CreateDiagnosisRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_run_id: str = Field(pattern=r"^evr_[0-9a-f]{32}$")


class DiagnosisRunPageResponse(DiagnosisRunPage):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


class DiagnosisRunDetailResponse(DiagnosisRunDetail):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


class DiagnosisRunMutationResponse(DiagnosisRunMutationResult):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)
