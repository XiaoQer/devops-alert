from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Response

from incident_intelligence.adapters.alertmanager import (
    AlertmanagerWebhook,
    to_signal_commands,
)
from incident_intelligence.api.dependencies import (
    get_signal_intake_service,
    require_alertmanager_actor,
)
from incident_intelligence.api.schemas.intake import IntakeBatchResponse
from incident_intelligence.services.signal_intake import SignalIntakeService

router = APIRouter(prefix="/api/v1/intake/alertmanager", tags=["signal-intake"])


@router.post("", response_model=IntakeBatchResponse, status_code=202)
def receive_alertmanager(
    webhook: AlertmanagerWebhook,
    response: Response,
    actor: Annotated[str, Depends(require_alertmanager_actor)],
    service: Annotated[SignalIntakeService, Depends(get_signal_intake_service)],
) -> IntakeBatchResponse:
    commands = to_signal_commands(webhook, datetime.now(UTC))
    result = service.submit_batch(
        commands,
        actor=actor,
        request_id=f"req_{uuid4().hex}",
    )
    response.status_code = 200 if all(item.replayed for item in result.items) else 202
    return IntakeBatchResponse.from_result(result)
