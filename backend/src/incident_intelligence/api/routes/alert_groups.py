from __future__ import annotations

from datetime import UTC, datetime
from re import compile as compile_pattern
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from pydantic import ValidationError

from incident_intelligence.api.dependencies import (
    get_alert_event_operation_service,
    get_alert_group_center_service,
    get_alert_regrouping_service,
    require_idempotency_key,
    require_manual_actor,
)
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.alert_groups import (
    AlertEventOperationResponse,
    AlertGroupMemberPageResponse,
    AlertGroupOverviewResponse,
    AlertGroupPageResponse,
    AlertGroupSummaryResponse,
    AlertRegroupRequest,
    AlertRegroupResponse,
    ConfirmAlertEventMemberRequest,
    MergeAlertEventsRequest,
    SplitAlertEventMembersRequest,
)
from incident_intelligence.domain.models import Environment, Severity
from incident_intelligence.services.alert_event_operations import (
    AlertEventCapacityConflict,
    AlertEventEnvironmentConflict,
    AlertEventIncidentConflict,
    AlertEventMemberConflict,
    AlertEventOperationConflict,
    AlertEventOperationService,
    AlertEventVersionConflict,
)
from incident_intelligence.services.alert_group_center import (
    AlertGroupCenterService,
    AlertGroupFilters,
    AlertGroupResourceNotFound,
    GroupState,
    StormState,
    SummaryWindow,
)
from incident_intelligence.services.alert_regrouping import AlertRegroupingService

router = APIRouter(prefix="/api/v1/alert-groups", tags=["alert-groups"])
ManualActor = Annotated[str, Depends(require_manual_actor)]
GroupService = Annotated[AlertGroupCenterService, Depends(get_alert_group_center_service)]
RegroupService = Annotated[AlertRegroupingService, Depends(get_alert_regrouping_service)]
OperationService = Annotated[AlertEventOperationService, Depends(get_alert_event_operation_service)]
IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]
GROUP_ID = compile_pattern(r"^agr_[0-9a-f]{32}$")
ALERT_ID = compile_pattern(r"^alt_[0-9a-f]{32}$")


@router.get("", response_model=AlertGroupPageResponse)
def list_groups(
    actor: ManualActor,
    center: GroupService,
    state: Annotated[GroupState | None, Query()] = None,
    severity: Annotated[Severity | None, Query()] = None,
    service_name: Annotated[str | None, Query(alias="service", max_length=128)] = None,
    environment: Annotated[Environment | None, Query()] = None,
    storm_state: Annotated[StormState | None, Query()] = None,
    incident_linked: Annotated[bool | None, Query()] = None,
    observed_from: Annotated[datetime | None, Query()] = None,
    observed_to: Annotated[datetime | None, Query()] = None,
    query: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> AlertGroupPageResponse:
    del actor
    try:
        filters = AlertGroupFilters(
            state=state,
            severity=severity,
            service=service_name,
            environment=environment,
            storm_state=storm_state,
            incident_linked=incident_linked,
            observed_from=observed_from,
            observed_to=observed_to,
            query=query,
            limit=limit,
            offset=offset,
        )
    except ValidationError as error:
        raise ApiError(422, "validation_error", "告警组筛选条件不符合约束") from error
    return AlertGroupPageResponse.model_validate(center.list_groups(filters).model_dump())


@router.get("/summary", response_model=AlertGroupSummaryResponse)
def summarize_groups(
    actor: ManualActor,
    center: GroupService,
    window: Annotated[SummaryWindow, Query()] = "24h",
) -> AlertGroupSummaryResponse:
    del actor
    return AlertGroupSummaryResponse.model_validate(
        center.summarize(window, now=datetime.now(UTC)).model_dump()
    )


@router.post("/regroup", response_model=AlertRegroupResponse)
def regroup_legacy_groups(
    command: AlertRegroupRequest,
    actor: ManualActor,
    service: RegroupService,
    request_id: Annotated[str, Header(alias="X-Request-ID", min_length=1, max_length=64)],
) -> AlertRegroupResponse:
    result = service.regroup_active_unlinked_groups(
        limit=command.limit,
        actor=actor,
        request_id=request_id,
    )
    return AlertRegroupResponse.model_validate(result.model_dump())


@router.post(
    "/{group_id}/members/{alert_id}/confirm",
    response_model=AlertEventOperationResponse,
)
def confirm_group_member(
    group_id: str,
    alert_id: str,
    command: ConfirmAlertEventMemberRequest,
    actor: ManualActor,
    service: OperationService,
    idempotency_key: IdempotencyKey,
    request_id: Annotated[str, Header(alias="X-Request-ID", min_length=1, max_length=64)],
) -> AlertEventOperationResponse:
    _require_group_id(group_id)
    _require_alert_id(alert_id)
    try:
        result = service.confirm_member(
            group_id,
            alert_id,
            command,
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    except Exception as error:
        raise _operation_error(error) from error
    return AlertEventOperationResponse.model_validate(result.model_dump())


@router.post("/{group_id}/members/split", response_model=AlertEventOperationResponse)
def split_group_members(
    group_id: str,
    command: SplitAlertEventMembersRequest,
    actor: ManualActor,
    service: OperationService,
    idempotency_key: IdempotencyKey,
    request_id: Annotated[str, Header(alias="X-Request-ID", min_length=1, max_length=64)],
) -> AlertEventOperationResponse:
    _require_group_id(group_id)
    try:
        result = service.split_members(
            group_id,
            command,
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    except Exception as error:
        raise _operation_error(error) from error
    return AlertEventOperationResponse.model_validate(result.model_dump())


@router.post("/{group_id}/merge", response_model=AlertEventOperationResponse)
def merge_alert_groups(
    group_id: str,
    command: MergeAlertEventsRequest,
    actor: ManualActor,
    service: OperationService,
    idempotency_key: IdempotencyKey,
    request_id: Annotated[str, Header(alias="X-Request-ID", min_length=1, max_length=64)],
) -> AlertEventOperationResponse:
    _require_group_id(group_id)
    _require_group_id(command.source_group_id)
    try:
        result = service.merge_events(
            group_id,
            command,
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    except Exception as error:
        raise _operation_error(error) from error
    return AlertEventOperationResponse.model_validate(result.model_dump())


@router.get("/{group_id}/overview", response_model=AlertGroupOverviewResponse)
def get_group_overview(
    group_id: str, actor: ManualActor, center: GroupService
) -> AlertGroupOverviewResponse:
    del actor
    _require_group_id(group_id)
    try:
        return AlertGroupOverviewResponse.model_validate(center.get_overview(group_id).model_dump())
    except AlertGroupResourceNotFound as error:
        raise _not_found() from error


@router.get("/{group_id}/alerts", response_model=AlertGroupMemberPageResponse)
def list_group_alerts(
    group_id: str,
    actor: ManualActor,
    center: GroupService,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> AlertGroupMemberPageResponse:
    del actor
    _require_group_id(group_id)
    try:
        return AlertGroupMemberPageResponse.model_validate(
            center.list_members(group_id, limit=limit, offset=offset).model_dump()
        )
    except AlertGroupResourceNotFound as error:
        raise _not_found() from error


def _require_group_id(group_id: str) -> None:
    if GROUP_ID.fullmatch(group_id) is None:
        raise _not_found()


def _require_alert_id(alert_id: str) -> None:
    if ALERT_ID.fullmatch(alert_id) is None:
        raise ApiError(404, "resource_not_found", "未找到指定告警")


def _operation_error(error: Exception) -> ApiError:
    if isinstance(error, AlertGroupResourceNotFound):
        return _not_found()
    if isinstance(error, AlertEventVersionConflict):
        return ApiError(409, error.reason_code, "告警事件已发生变化。请刷新后再试")
    if isinstance(error, AlertEventOperationConflict):
        return ApiError(409, error.reason_code, "该幂等键已用于不同操作")
    if isinstance(error, AlertEventEnvironmentConflict):
        return ApiError(409, error.reason_code, "不同环境的告警事件不能合并")
    if isinstance(error, AlertEventIncidentConflict):
        return ApiError(409, error.reason_code, "已关联不同事故的告警事件不能合并")
    if isinstance(error, AlertEventCapacityConflict):
        return ApiError(409, error.reason_code, "操作后将超过告警事件容量上限")
    if isinstance(error, AlertEventMemberConflict):
        status = 404 if error.reason_code.endswith("_not_found") else 409
        return ApiError(status, error.reason_code, "告警成员状态不允许执行该操作")
    raise error


def _not_found() -> ApiError:
    return ApiError(404, "resource_not_found", "未找到指定告警组")
