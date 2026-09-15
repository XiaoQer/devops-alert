from __future__ import annotations

from typing import Annotated, Never

from fastapi import APIRouter, Depends

from incident_intelligence.api.dependencies import (
    get_diagnosis_tool_service,
    require_diagnosis_capability,
)
from incident_intelligence.api.errors import ApiError
from incident_intelligence.services.diagnosis_capabilities import DiagnosisCapabilityDenied
from incident_intelligence.services.diagnosis_tools import (
    DiagnosisEvidenceScopeDenied,
    DiagnosisToolRunNotFound,
    DiagnosisToolService,
)

router = APIRouter(prefix="/api/v1/diagnosis-runs", tags=["diagnosis-tools"])
CapabilityToken = Annotated[str, Depends(require_diagnosis_capability)]
Service = Annotated[DiagnosisToolService, Depends(get_diagnosis_tool_service)]


@router.get("/{diagnosis_run_id}/tools/snapshot")
def get_diagnosis_snapshot(
    diagnosis_run_id: str,
    capability_token: CapabilityToken,
    service: Service,
) -> object:
    try:
        return service.get_diagnosis_snapshot(
            diagnosis_run_id,
            capability_token=capability_token,
        )
    except (DiagnosisCapabilityDenied, DiagnosisToolRunNotFound) as error:
        _raise_tool_error(error)


@router.get("/{diagnosis_run_id}/tools/evidence/{evidence_item_id}")
def get_evidence_detail(
    diagnosis_run_id: str,
    evidence_item_id: str,
    capability_token: CapabilityToken,
    service: Service,
) -> object:
    try:
        return service.get_evidence_detail(
            diagnosis_run_id,
            capability_token=capability_token,
            evidence_item_id=evidence_item_id,
        )
    except (
        DiagnosisCapabilityDenied,
        DiagnosisToolRunNotFound,
        DiagnosisEvidenceScopeDenied,
    ) as error:
        _raise_tool_error(error)


def _raise_tool_error(
    error: DiagnosisCapabilityDenied | DiagnosisToolRunNotFound | DiagnosisEvidenceScopeDenied,
) -> Never:
    if isinstance(error, DiagnosisCapabilityDenied):
        raise ApiError(403, error.reason_code, "诊断能力凭证无效或已过期") from error
    if isinstance(error, DiagnosisEvidenceScopeDenied):
        raise ApiError(403, error.reason_code, "证据不属于当前诊断范围") from error
    raise ApiError(404, error.reason_code, "未找到诊断运行") from error
