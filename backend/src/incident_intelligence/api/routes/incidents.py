from __future__ import annotations

from re import compile as compile_pattern
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Query

from incident_intelligence.api.dependencies import (
    get_incident_center_service,
    require_manual_actor,
)
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.incidents import (
    IncidentClaimResponse,
    IncidentListResponse,
    IncidentOverviewResponse,
)
from incident_intelligence.domain.enums import IncidentState
from incident_intelligence.domain.models import Environment
from incident_intelligence.services.incident_center import (
    IncidentAlreadyClaimed,
    IncidentCenterService,
    IncidentListQuery,
    IncidentNotClaimable,
    IncidentResourceNotFound,
)

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])
ManualActor = Annotated[str, Depends(require_manual_actor)]
IncidentService = Annotated[IncidentCenterService, Depends(get_incident_center_service)]
INCIDENT_ID = compile_pattern(r"^inc_[0-9a-f]{32}$")


@router.get("", response_model=IncidentListResponse)
def list_incidents(
    actor: ManualActor,
    service: IncidentService,
    environment: Annotated[Environment | None, Query()] = None,
    state: Annotated[IncidentState | None, Query()] = None,
    query: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> IncidentListResponse:
    del actor
    result = service.list_incidents(
        IncidentListQuery(
            environment=environment,
            state=None if state is None else state.value,
            query=None if query is None or not query.strip() else query.strip(),
            limit=limit,
            offset=offset,
        )
    )
    return IncidentListResponse.model_validate(result, from_attributes=True)


@router.get("/{incident_id}/overview", response_model=IncidentOverviewResponse)
def get_incident_overview(
    incident_id: str,
    actor: ManualActor,
    service: IncidentService,
) -> IncidentOverviewResponse:
    del actor
    _require_incident_id(incident_id)
    try:
        return IncidentOverviewResponse.model_validate(
            service.get_overview(incident_id), from_attributes=True
        )
    except IncidentResourceNotFound as error:
        raise _not_found() from error


@router.post("/{incident_id}/claim", response_model=IncidentClaimResponse)
def claim_incident(
    incident_id: str,
    actor: ManualActor,
    service: IncidentService,
) -> IncidentClaimResponse:
    _require_incident_id(incident_id)
    try:
        result = service.claim(
            incident_id,
            actor=actor,
            request_id=f"req_{uuid4().hex}",
        )
    except IncidentResourceNotFound as error:
        raise _not_found() from error
    except IncidentAlreadyClaimed as error:
        raise ApiError(409, "incident_already_claimed", "事故已由其他处理人认领") from error
    except IncidentNotClaimable as error:
        raise ApiError(409, "incident_not_claimable", "当前事故状态不可认领") from error
    return IncidentClaimResponse.model_validate(result, from_attributes=True)


def _require_incident_id(incident_id: str) -> None:
    if INCIDENT_ID.fullmatch(incident_id) is None:
        raise _not_found()


def _not_found() -> ApiError:
    return ApiError(404, "resource_not_found", "未找到指定资源")
