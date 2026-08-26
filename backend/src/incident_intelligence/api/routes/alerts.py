from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError

from incident_intelligence.api.dependencies import (
    get_alert_center_service,
    require_manual_actor,
)
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.alerts import (
    AlertOverviewResponse,
    AlertPageResponse,
    AlertSummaryResponse,
)
from incident_intelligence.domain.models import Environment, Severity
from incident_intelligence.services.alert_center import (
    AlertCenterService,
    AlertListFilters,
    AlertResourceNotFound,
    AlertStateValue,
    SummaryWindow,
)

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])
ManualActor = Annotated[str, Depends(require_manual_actor)]
AlertService = Annotated[AlertCenterService, Depends(get_alert_center_service)]


@router.get("", response_model=AlertPageResponse)
def list_alerts(
    actor: ManualActor,
    service: AlertService,
    alert_source_id: Annotated[str | None, Query()] = None,
    state: Annotated[AlertStateValue | None, Query()] = None,
    severity: Annotated[Severity | None, Query()] = None,
    service_name: Annotated[str | None, Query(alias="service", max_length=128)] = None,
    environment: Annotated[Environment | None, Query()] = None,
    incident_linked: Annotated[bool | None, Query()] = None,
    observed_from: Annotated[datetime | None, Query()] = None,
    observed_to: Annotated[datetime | None, Query()] = None,
    query: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> AlertPageResponse:
    del actor
    try:
        filters = AlertListFilters(
            alert_source_id=alert_source_id,
            state=state,
            severity=severity,
            service=service_name,
            environment=environment,
            incident_linked=incident_linked,
            observed_from=observed_from,
            observed_to=observed_to,
            query=query,
            limit=limit,
            offset=offset,
        )
    except ValidationError as error:
        raise ApiError(422, "validation_error", "告警筛选条件不符合约束") from error
    return AlertPageResponse.model_validate(service.list_alerts(filters).model_dump())


@router.get("/summary", response_model=AlertSummaryResponse)
def summarize_alerts(
    actor: ManualActor,
    service: AlertService,
    window: Annotated[SummaryWindow, Query()] = "24h",
) -> AlertSummaryResponse:
    del actor
    return AlertSummaryResponse.model_validate(
        service.summarize(window, now=datetime.now(UTC)).model_dump()
    )


@router.get("/{alert_id}/overview", response_model=AlertOverviewResponse)
def get_alert_overview(
    alert_id: str,
    actor: ManualActor,
    service: AlertService,
) -> AlertOverviewResponse:
    del actor
    try:
        result = service.get_overview(alert_id)
    except AlertResourceNotFound as error:
        raise ApiError(404, "resource_not_found", "未找到指定告警") from error
    return AlertOverviewResponse.model_validate(result.model_dump())
