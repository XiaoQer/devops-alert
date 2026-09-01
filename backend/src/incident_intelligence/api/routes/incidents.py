# ruff: noqa: RUF001

from __future__ import annotations

from typing import Annotated, Literal, Never
from uuid import uuid4

from fastapi import APIRouter, Depends, Query

from incident_intelligence.api.dependencies import (
    get_incident_service,
    require_idempotency_key,
    require_manual_actor,
)
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.incidents import (
    IncidentDetailResponse,
    IncidentMutationResponse,
    IncidentPageResponse,
    IncidentVersionRequest,
    ResolveIncidentRequest,
)
from incident_intelligence.domain.incidents import IncidentStateConflict
from incident_intelligence.services.incidents import (
    IncidentIdempotencyConflict,
    IncidentNotFound,
    IncidentService,
    IncidentVersionConflict,
)

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])
ManualActor = Annotated[str, Depends(require_manual_actor)]
IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]
Service = Annotated[IncidentService, Depends(get_incident_service)]
IncidentState = Literal["OPEN", "ACKNOWLEDGED", "RESOLVED"]
IncidentError = (
    IncidentNotFound | IncidentVersionConflict | IncidentIdempotencyConflict | IncidentStateConflict
)
INCIDENT_ERRORS = (
    IncidentNotFound,
    IncidentVersionConflict,
    IncidentIdempotencyConflict,
    IncidentStateConflict,
)


@router.get("", response_model=IncidentPageResponse)
def list_incidents(
    actor: ManualActor,
    service: Service,
    states: Annotated[list[IncidentState] | None, Query(alias="state")] = None,
    environment: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
    severity: Annotated[
        Literal["low", "medium", "high", "critical"] | None,
        Query(),
    ] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> IncidentPageResponse:
    del actor
    selected_states = ("OPEN", "ACKNOWLEDGED") if states is None else tuple(states)
    return IncidentPageResponse.model_validate(
        service.list(
            states=selected_states,
            environment=environment,
            severity=severity,
            search=search,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/{incident_id}", response_model=IncidentDetailResponse)
def get_incident(
    incident_id: str,
    actor: ManualActor,
    service: Service,
) -> IncidentDetailResponse:
    del actor
    try:
        return IncidentDetailResponse.model_validate(service.get(incident_id))
    except INCIDENT_ERRORS as error:
        _raise_incident_error(error)


@router.post("/{incident_id}/acknowledge", response_model=IncidentMutationResponse)
def acknowledge_incident(
    incident_id: str,
    request: IncidentVersionRequest,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: Service,
) -> IncidentMutationResponse:
    try:
        return IncidentMutationResponse.model_validate(
            service.acknowledge(
                incident_id,
                expected_version=request.expected_version,
                idempotency_key=idempotency_key,
                actor=actor,
                request_id=_request_id(),
            )
        )
    except INCIDENT_ERRORS as error:
        _raise_incident_error(error)


@router.post("/{incident_id}/resolve", response_model=IncidentMutationResponse)
def resolve_incident(
    incident_id: str,
    request: ResolveIncidentRequest,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: Service,
) -> IncidentMutationResponse:
    try:
        return IncidentMutationResponse.model_validate(
            service.resolve(
                incident_id,
                expected_version=request.expected_version,
                resolution_summary=request.resolution_summary,
                idempotency_key=idempotency_key,
                actor=actor,
                request_id=_request_id(),
            )
        )
    except INCIDENT_ERRORS as error:
        _raise_incident_error(error)


def _request_id() -> str:
    return f"req_{uuid4().hex}"


def _raise_incident_error(error: IncidentError) -> Never:
    if isinstance(error, IncidentNotFound):
        raise ApiError(404, error.reason_code, "未找到指定 Incident") from error
    if isinstance(error, IncidentVersionConflict):
        raise ApiError(409, error.reason_code, "Incident 已更新，请刷新后重试") from error
    if isinstance(error, IncidentIdempotencyConflict):
        raise ApiError(409, error.reason_code, "幂等键已用于不同操作") from error
    raise ApiError(409, "incident_state_conflict", "当前 Incident 状态不允许此操作") from error
