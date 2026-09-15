from __future__ import annotations

from typing import Annotated, Never
from uuid import uuid4

from fastapi import APIRouter, Depends, Query

from incident_intelligence.api.dependencies import (
    get_incident_diagnosis_service,
    require_idempotency_key,
    require_manual_actor,
)
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.incident_diagnosis import (
    CreateDiagnosisRunRequest,
    DiagnosisRunDetailResponse,
    DiagnosisRunMutationResponse,
    DiagnosisRunPageResponse,
)
from incident_intelligence.services.incident_diagnosis import (
    DiagnosisEvidenceRunInvalid,
    DiagnosisRunAlreadyActive,
    DiagnosisRunNotFound,
    IncidentDiagnosisService,
)

router = APIRouter(prefix="/api/v1/incidents", tags=["incident-diagnosis"])
ManualActor = Annotated[str, Depends(require_manual_actor)]
IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]
Service = Annotated[IncidentDiagnosisService, Depends(get_incident_diagnosis_service)]


@router.post("/{incident_id}/diagnosis-runs", response_model=DiagnosisRunMutationResponse)
def create_diagnosis_run(
    incident_id: str,
    request: CreateDiagnosisRunRequest,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: Service,
) -> DiagnosisRunMutationResponse:
    try:
        return DiagnosisRunMutationResponse.model_validate(
            service.request_manual(
                incident_id,
                evidence_run_id=request.evidence_run_id,
                actor=actor,
                idempotency_key=idempotency_key,
                request_id=f"req_{uuid4().hex}",
            )
        )
    except (DiagnosisRunNotFound, DiagnosisEvidenceRunInvalid, DiagnosisRunAlreadyActive) as error:
        _raise_diagnosis_error(error)


@router.get("/{incident_id}/diagnosis-runs", response_model=DiagnosisRunPageResponse)
def list_diagnosis_runs(
    incident_id: str,
    actor: ManualActor,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> DiagnosisRunPageResponse:
    del actor
    try:
        return DiagnosisRunPageResponse.model_validate(
            service.list_runs(incident_id, limit=limit, offset=offset)
        )
    except DiagnosisRunNotFound as error:
        _raise_diagnosis_error(error)


@router.get("/{incident_id}/diagnosis-runs/{run_id}", response_model=DiagnosisRunDetailResponse)
def get_diagnosis_run(
    incident_id: str,
    run_id: str,
    actor: ManualActor,
    service: Service,
) -> DiagnosisRunDetailResponse:
    del actor
    try:
        return DiagnosisRunDetailResponse.model_validate(service.get_run(incident_id, run_id))
    except DiagnosisRunNotFound as error:
        _raise_diagnosis_error(error)


def _raise_diagnosis_error(
    error: DiagnosisRunNotFound | DiagnosisEvidenceRunInvalid | DiagnosisRunAlreadyActive,
) -> Never:
    if isinstance(error, DiagnosisRunNotFound):
        raise ApiError(404, error.reason_code, "未找到指定 Incident 或诊断运行") from error
    if isinstance(error, DiagnosisRunAlreadyActive):
        raise ApiError(409, error.reason_code, "当前已有诊断正在运行") from error
    raise ApiError(409, error.reason_code, "当前证据运行不能用于诊断") from error
