
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Query

from incident_intelligence.api.dependencies import (
    get_correlation_job_service,
    get_correlation_read_service,
    require_manual_actor,
)
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.correlation import (
    AlertCorrelationResponse,
    CorrelationJobListResponse,
    CorrelationJobResponse,
)
from incident_intelligence.domain.enums import CorrelationJobState
from incident_intelligence.services.correlation import (
    CorrelationReadService,
    CorrelationResourceNotFound,
)
from incident_intelligence.services.correlation_jobs import (
    CorrelationJobNotFound,
    CorrelationJobNotRetryable,
    CorrelationJobService,
)

router = APIRouter(tags=["correlation"])
ManualActor = Annotated[str, Depends(require_manual_actor)]
ReadService = Annotated[CorrelationReadService, Depends(get_correlation_read_service)]
JobService = Annotated[CorrelationJobService, Depends(get_correlation_job_service)]


@router.get(
    "/api/v1/alerts/{alert_id}/correlation",
    response_model=AlertCorrelationResponse,
)
def get_alert_correlation(
    alert_id: str,
    actor: ManualActor,
    service: ReadService,
) -> AlertCorrelationResponse:
    del actor
    try:
        return AlertCorrelationResponse.model_validate(
            service.get_alert_correlation(alert_id)
        )
    except CorrelationResourceNotFound as error:
        raise ApiError(404, "resource_not_found", "未找到指定资源") from error


@router.get("/api/v1/correlation/jobs", response_model=CorrelationJobListResponse)
def list_correlation_jobs(
    actor: ManualActor,
    service: ReadService,
    state: Annotated[CorrelationJobState | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> CorrelationJobListResponse:
    del actor
    items = service.list_jobs(
        state=None if state is None else state.value,
        limit=limit,
        offset=offset,
    )
    return CorrelationJobListResponse(
        items=tuple(CorrelationJobResponse.model_validate(item) for item in items),
        limit=limit,
        offset=offset,
    )


@router.post(
    "/api/v1/correlation/jobs/{job_id}/retry",
    response_model=CorrelationJobResponse,
)
def retry_correlation_job(
    job_id: str,
    actor: ManualActor,
    service: JobService,
    read_service: ReadService,
) -> CorrelationJobResponse:
    try:
        service.retry_failed(
            job_id,
            datetime.now(UTC),
            actor=actor,
            request_id=f"req_{uuid4().hex}",
        )
    except CorrelationJobNotFound as error:
        raise ApiError(404, "resource_not_found", "未找到指定资源") from error
    except CorrelationJobNotRetryable as error:
        raise ApiError(
            409,
            "correlation_job_not_retryable",
            "只有失败的关联任务可以重试",
        ) from error
    return CorrelationJobResponse.model_validate(read_service.get_job(job_id))
