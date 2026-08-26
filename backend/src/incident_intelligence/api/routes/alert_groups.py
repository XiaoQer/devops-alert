from __future__ import annotations

from datetime import UTC, datetime
from re import compile as compile_pattern
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError

from incident_intelligence.api.dependencies import (
    get_alert_group_center_service,
    require_manual_actor,
)
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.alert_groups import (
    AlertGroupMemberPageResponse,
    AlertGroupOverviewResponse,
    AlertGroupPageResponse,
    AlertGroupSummaryResponse,
)
from incident_intelligence.domain.models import Environment, Severity
from incident_intelligence.services.alert_group_center import (
    AlertGroupCenterService,
    AlertGroupFilters,
    AlertGroupResourceNotFound,
    GroupState,
    StormState,
    SummaryWindow,
)

router = APIRouter(prefix="/api/v1/alert-groups", tags=["alert-groups"])
ManualActor = Annotated[str, Depends(require_manual_actor)]
GroupService = Annotated[AlertGroupCenterService, Depends(get_alert_group_center_service)]
GROUP_ID = compile_pattern(r"^agr_[0-9a-f]{32}$")


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


def _not_found() -> ApiError:
    return ApiError(404, "resource_not_found", "未找到指定告警组")
