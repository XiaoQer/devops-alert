from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Response

from incident_intelligence.api.dependencies import (
    get_manual_intake_service,
    require_idempotency_key,
    require_manual_actor,
)
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.manual_reports import (
    ManualReportRequest,
    ManualReportResponse,
)
from incident_intelligence.services.manual_intake import (
    IdempotencyConflict,
    ManualIntakeCommand,
    ManualIntakeService,
)

router = APIRouter(prefix="/api/v1/manual-reports", tags=["manual-reports"])


@router.post("", response_model=ManualReportResponse, status_code=201)
def create_manual_report(
    report: ManualReportRequest,
    response: Response,
    actor: Annotated[str, Depends(require_manual_actor)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    service: Annotated[ManualIntakeService, Depends(get_manual_intake_service)],
) -> ManualReportResponse:
    command = ManualIntakeCommand(
        title=report.title,
        summary=report.summary,
        severity=report.severity,
        service=report.service,
        environment=report.environment,
        observed_at=report.observed_at,
        facts=report.labels,
    )
    try:
        result = service.submit(
            command,
            idempotency_key=idempotency_key,
            actor=actor,
            request_id=f"req_{uuid4().hex}",
        )
    except IdempotencyConflict as error:
        raise ApiError(409, error.reason_code, "幂等键已用于另一份报告") from error

    response.status_code = 200 if result.replayed else 201
    return ManualReportResponse(**result.model_dump())
