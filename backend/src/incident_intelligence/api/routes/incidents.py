from __future__ import annotations

from collections.abc import Callable
from re import compile as compile_pattern
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Query

from incident_intelligence.api.dependencies import (
    get_alert_group_center_service,
    get_incident_center_service,
    get_incident_operation_service,
    require_idempotency_key,
    require_manual_actor,
)
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.alert_groups import AlertGroupPageResponse
from incident_intelligence.api.schemas.incidents import (
    IncidentClaimRequest,
    IncidentCloseRequest,
    IncidentListResponse,
    IncidentNoteRequest,
    IncidentOperationResponse,
    IncidentOverviewResponse,
    IncidentReleaseRequest,
    IncidentReopenRequest,
    IncidentResolveRequest,
    IncidentTransitionRequest,
)
from incident_intelligence.domain.enums import IncidentState
from incident_intelligence.domain.models import Environment
from incident_intelligence.services.alert_group_center import (
    AlertGroupCenterService,
    AlertGroupResourceNotFound,
)
from incident_intelligence.services.incident_center import (
    IncidentCenterService,
    IncidentListQuery,
    IncidentResourceNotFound,
)
from incident_intelligence.services.incident_operations import (
    IncidentAlreadyClaimed,
    IncidentClosed,
    IncidentNotClaimed,
    IncidentOperationConflict,
    IncidentOperationError,
    IncidentOperationService,
    IncidentVersionConflict,
    InvalidIncidentOperation,
    InvalidIncidentTransition,
)

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])
ManualActor = Annotated[str, Depends(require_manual_actor)]
IncidentService = Annotated[IncidentCenterService, Depends(get_incident_center_service)]
GroupService = Annotated[AlertGroupCenterService, Depends(get_alert_group_center_service)]
OperationService = Annotated[IncidentOperationService, Depends(get_incident_operation_service)]
IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]
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
    _require_incident_id(incident_id)
    try:
        return IncidentOverviewResponse.model_validate(
            service.get_overview(incident_id, actor=actor), from_attributes=True
        )
    except IncidentResourceNotFound as error:
        raise _not_found() from error


@router.get("/{incident_id}/alert-groups", response_model=AlertGroupPageResponse)
def list_incident_alert_groups(
    incident_id: str,
    actor: ManualActor,
    center: GroupService,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> AlertGroupPageResponse:
    del actor
    _require_incident_id(incident_id)
    try:
        return AlertGroupPageResponse.model_validate(
            center.list_incident_groups(incident_id, limit=limit, offset=offset).model_dump()
        )
    except AlertGroupResourceNotFound as error:
        raise _not_found() from error


@router.post("/{incident_id}/claim", response_model=IncidentOperationResponse)
def claim_incident(
    incident_id: str,
    command: IncidentClaimRequest,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: OperationService,
) -> IncidentOperationResponse:
    _require_incident_id(incident_id)
    try:
        result = service.claim(
            incident_id,
            expected_version=command.expected_version,
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=f"req_{uuid4().hex}",
        )
    except (IncidentResourceNotFound, IncidentOperationError) as error:
        raise _operation_error(error) from error
    return IncidentOperationResponse.model_validate(result, from_attributes=True)


@router.post("/{incident_id}/release", response_model=IncidentOperationResponse)
def release_incident(
    incident_id: str,
    command: IncidentReleaseRequest,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: OperationService,
) -> IncidentOperationResponse:
    return _run_operation(
        incident_id,
        lambda: service.release(
            incident_id,
            expected_version=command.expected_version,
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=f"req_{uuid4().hex}",
        ),
    )


@router.post("/{incident_id}/transitions", response_model=IncidentOperationResponse)
def transition_incident(
    incident_id: str,
    command: IncidentTransitionRequest,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: OperationService,
) -> IncidentOperationResponse:
    return _run_operation(
        incident_id,
        lambda: service.transition(
            incident_id,
            expected_version=command.expected_version,
            target_state=command.target_state,
            message=command.message,
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=f"req_{uuid4().hex}",
        ),
    )


@router.post("/{incident_id}/notes", response_model=IncidentOperationResponse)
def add_incident_note(
    incident_id: str,
    command: IncidentNoteRequest,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: OperationService,
) -> IncidentOperationResponse:
    return _run_operation(
        incident_id,
        lambda: service.add_note(
            incident_id,
            expected_version=command.expected_version,
            category=command.category,
            message=command.message,
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=f"req_{uuid4().hex}",
        ),
    )


@router.post("/{incident_id}/resolve", response_model=IncidentOperationResponse)
def resolve_incident(
    incident_id: str,
    command: IncidentResolveRequest,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: OperationService,
) -> IncidentOperationResponse:
    return _run_operation(
        incident_id,
        lambda: service.resolve(
            incident_id,
            expected_version=command.expected_version,
            category=command.category,
            message=command.message,
            resolution_actions=command.resolution_actions,
            root_cause=command.root_cause,
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=f"req_{uuid4().hex}",
        ),
    )


@router.post("/{incident_id}/reopen", response_model=IncidentOperationResponse)
def reopen_incident(
    incident_id: str,
    command: IncidentReopenRequest,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: OperationService,
) -> IncidentOperationResponse:
    return _run_operation(
        incident_id,
        lambda: service.reopen(
            incident_id,
            expected_version=command.expected_version,
            reason=command.reason,
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=f"req_{uuid4().hex}",
        ),
    )


@router.post("/{incident_id}/close", response_model=IncidentOperationResponse)
def close_incident(
    incident_id: str,
    command: IncidentCloseRequest,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: OperationService,
) -> IncidentOperationResponse:
    return _run_operation(
        incident_id,
        lambda: service.close(
            incident_id,
            expected_version=command.expected_version,
            message=command.message,
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=f"req_{uuid4().hex}",
        ),
    )


def _run_operation(
    incident_id: str,
    operation: Callable[[], object],
) -> IncidentOperationResponse:
    _require_incident_id(incident_id)
    try:
        result = operation()
    except (IncidentResourceNotFound, IncidentOperationError) as error:
        raise _operation_error(error) from error
    return IncidentOperationResponse.model_validate(result, from_attributes=True)


def _operation_error(error: IncidentResourceNotFound | IncidentOperationError) -> ApiError:
    if isinstance(error, IncidentResourceNotFound):
        return ApiError(404, "incident_not_found", "未找到指定事故")
    mappings = {
        IncidentVersionConflict: (
            "incident_version_conflict",
            "事故已被其他操作更新，请刷新后重试",  # noqa: RUF001
        ),
        IncidentOperationConflict: (
            "incident_operation_conflict",
            "幂等键已用于其他事故操作",
        ),
        IncidentAlreadyClaimed: ("incident_already_claimed", "事故已由其他处理人认领"),
        IncidentNotClaimed: ("incident_not_claimed", "事故未由当前处理人认领"),
        IncidentClosed: ("incident_closed", "事故关闭后不能继续操作"),
        InvalidIncidentTransition: (
            "invalid_incident_transition",
            "当前事故状态不允许执行该状态变更",
        ),
        InvalidIncidentOperation: (
            error.reason_code,
            "当前事故状态不允许执行该操作",
        ),
    }
    code, message = mappings.get(
        type(error), ("invalid_incident_operation", "当前事故状态不允许执行该操作")
    )
    return ApiError(409, code, message)


def _require_incident_id(incident_id: str) -> None:
    if INCIDENT_ID.fullmatch(incident_id) is None:
        raise _not_found()


def _not_found() -> ApiError:
    return ApiError(404, "resource_not_found", "未找到指定资源")
