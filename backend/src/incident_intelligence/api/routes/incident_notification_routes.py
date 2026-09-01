# ruff: noqa: RUF001

from __future__ import annotations

from typing import Annotated, Never
from uuid import uuid4

from fastapi import APIRouter, Depends, Response

from incident_intelligence.api.dependencies import (
    get_incident_notification_route_service,
    require_idempotency_key,
    require_manual_actor,
)
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.incident_notification_routes import (
    CreateIncidentNotificationRouteRequest,
    IncidentNotificationRouteMutationResponse,
    IncidentNotificationRoutePageResponse,
    UpdateIncidentNotificationRouteRequest,
)
from incident_intelligence.services.incident_notification_routes import (
    FeishuCredentialsIncomplete,
    IncidentNotificationRouteConflict,
    IncidentNotificationRouteIdempotencyConflict,
    IncidentNotificationRouteNotFound,
    IncidentNotificationRouteService,
    IncidentNotificationRouteVersionConflict,
)

router = APIRouter(
    prefix="/api/v1/incident-notification-routes",
    tags=["incident-notification-routes"],
)
ManualActor = Annotated[str, Depends(require_manual_actor)]
IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]
Service = Annotated[
    IncidentNotificationRouteService,
    Depends(get_incident_notification_route_service),
]
RouteError = (
    FeishuCredentialsIncomplete
    | IncidentNotificationRouteConflict
    | IncidentNotificationRouteIdempotencyConflict
    | IncidentNotificationRouteNotFound
    | IncidentNotificationRouteVersionConflict
)
ROUTE_ERRORS = (
    FeishuCredentialsIncomplete,
    IncidentNotificationRouteConflict,
    IncidentNotificationRouteIdempotencyConflict,
    IncidentNotificationRouteNotFound,
    IncidentNotificationRouteVersionConflict,
)


@router.get("", response_model=IncidentNotificationRoutePageResponse)
def list_routes(
    actor: ManualActor,
    service: Service,
) -> IncidentNotificationRoutePageResponse:
    del actor
    return IncidentNotificationRoutePageResponse.model_validate(service.list())


@router.post(
    "",
    response_model=IncidentNotificationRouteMutationResponse,
    status_code=201,
)
def create_route(
    request: CreateIncidentNotificationRouteRequest,
    response: Response,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: Service,
) -> IncidentNotificationRouteMutationResponse:
    try:
        result = service.create(
            **request.model_dump(),
            idempotency_key=idempotency_key,
            actor=actor,
            request_id=_request_id(),
        )
    except ROUTE_ERRORS as error:
        _raise_route_error(error)
    if result.replayed:
        response.status_code = 200
    return IncidentNotificationRouteMutationResponse.model_validate(result)


@router.patch(
    "/{route_id}",
    response_model=IncidentNotificationRouteMutationResponse,
)
def update_route(
    route_id: str,
    request: UpdateIncidentNotificationRouteRequest,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: Service,
) -> IncidentNotificationRouteMutationResponse:
    try:
        return IncidentNotificationRouteMutationResponse.model_validate(
            service.update(
                route_id,
                **request.model_dump(),
                idempotency_key=idempotency_key,
                actor=actor,
                request_id=_request_id(),
            )
        )
    except ROUTE_ERRORS as error:
        _raise_route_error(error)


def _request_id() -> str:
    return f"req_{uuid4().hex}"


def _raise_route_error(error: RouteError) -> Never:
    if isinstance(error, IncidentNotificationRouteNotFound):
        raise ApiError(404, error.reason_code, "未找到指定飞书通知路由") from error
    if isinstance(error, FeishuCredentialsIncomplete):
        missing = "、".join(error.missing_environment_keys)
        raise ApiError(409, error.reason_code, f"启用前需要配置：{missing}") from error
    if isinstance(error, IncidentNotificationRouteVersionConflict):
        raise ApiError(409, error.reason_code, "通知路由已更新，请刷新后重试") from error
    if isinstance(error, IncidentNotificationRouteIdempotencyConflict):
        raise ApiError(409, error.reason_code, "幂等键已用于不同操作") from error
    raise ApiError(409, error.reason_code, "同一环境只能启用一个飞书事故群") from error
