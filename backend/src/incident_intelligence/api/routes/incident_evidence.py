from __future__ import annotations

from typing import Annotated, Never
from uuid import uuid4

from fastapi import APIRouter, Depends, Query

from incident_intelligence.api.dependencies import (
    get_incident_evidence_service,
    require_idempotency_key,
    require_manual_actor,
)
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.incident_evidence import (
    EvidenceRunDetailResponse,
    EvidenceRunMutationResponse,
    EvidenceRunPageResponse,
)
from incident_intelligence.services.incident_evidence import (
    EvidenceIdempotencyConflict,
    EvidenceRunAlreadyActive,
    EvidenceRunNotFound,
    IncidentEvidenceService,
)

router = APIRouter(prefix="/api/v1/incidents", tags=["incident-evidence"])
ManualActor = Annotated[str, Depends(require_manual_actor)]
IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]
Service = Annotated[IncidentEvidenceService, Depends(get_incident_evidence_service)]


@router.get("/{incident_id}/evidence-runs", response_model=EvidenceRunPageResponse)
def list_evidence_runs(
    incident_id: str,
    actor: ManualActor,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> EvidenceRunPageResponse:
    del actor
    try:
        return EvidenceRunPageResponse.model_validate(
            service.list_runs(incident_id, limit=limit, offset=offset)
        )
    except (EvidenceRunNotFound, EvidenceIdempotencyConflict) as error:
        _raise_evidence_error(error)


@router.get(
    "/{incident_id}/evidence-runs/{run_id}",
    response_model=EvidenceRunDetailResponse,
)
def get_evidence_run(
    incident_id: str,
    run_id: str,
    actor: ManualActor,
    service: Service,
) -> EvidenceRunDetailResponse:
    del actor
    try:
        return EvidenceRunDetailResponse.model_validate(service.get_run(incident_id, run_id))
    except (EvidenceRunNotFound, EvidenceIdempotencyConflict) as error:
        _raise_evidence_error(error)


@router.post("/{incident_id}/evidence-runs", response_model=EvidenceRunMutationResponse)
def request_evidence_run(
    incident_id: str,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: Service,
) -> EvidenceRunMutationResponse:
    try:
        return EvidenceRunMutationResponse.model_validate(
            service.request_manual(
                incident_id,
                idempotency_key=idempotency_key,
                actor=actor,
                request_id=f"req_{uuid4().hex}",
            )
        )
    except (
        EvidenceRunNotFound,
        EvidenceRunAlreadyActive,
        EvidenceIdempotencyConflict,
    ) as error:
        _raise_evidence_error(error)


def _raise_evidence_error(
    error: EvidenceRunNotFound | EvidenceRunAlreadyActive | EvidenceIdempotencyConflict,
) -> Never:
    if isinstance(error, EvidenceRunNotFound):
        raise ApiError(404, error.reason_code, "未找到指定 Incident 或取证运行") from error
    if isinstance(error, EvidenceRunAlreadyActive):
        raise ApiError(
            409,
            error.reason_code,
            "当前已有取证任务正在执行",
            {"active_run_id": error.active_run_id},
        ) from error
    raise ApiError(409, error.reason_code, "幂等键已用于不同操作") from error
