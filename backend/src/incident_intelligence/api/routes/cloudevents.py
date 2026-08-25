from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import ValidationError

from incident_intelligence.adapters.cloudevents import (
    EVENT_TYPE,
    BinaryCloudEventContext,
    CloudEventData,
    StructuredCloudEvent,
    binary_to_signal_command,
    structured_to_signal_command,
)
from incident_intelligence.api.dependencies import (
    get_signal_intake_service,
    require_cloudevents_actor,
)
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.intake import IntakeBatchResponse
from incident_intelligence.domain.signal_intake import SignalCommand
from incident_intelligence.services.signal_intake import SignalIntakeService

router = APIRouter(prefix="/api/v1/intake/cloudevents", tags=["signal-intake"])


@router.post("", response_model=IntakeBatchResponse, status_code=202)
async def receive_cloudevent(
    request: Request,
    response: Response,
    actor: Annotated[str, Depends(require_cloudevents_actor)],
    service: Annotated[SignalIntakeService, Depends(get_signal_intake_service)],
) -> IntakeBatchResponse:
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().casefold()
    if content_type not in {"application/cloudevents+json", "application/json"}:
        raise ApiError(415, "unsupported_media_type", "不支持该事件内容类型")

    payload = await _read_json(request)
    command = _to_command(content_type, request, payload)
    result = service.submit_batch(
        (command,),
        actor=actor,
        request_id=f"req_{uuid4().hex}",
    )
    response.status_code = 200 if result.items[0].replayed else 202
    return IntakeBatchResponse.from_result(result)


async def _read_json(request: Request) -> Any:
    try:
        return await request.json()
    except (ValueError, UnicodeDecodeError) as error:
        raise ApiError(422, "validation_error", "请求字段不符合约束") from error


def _to_command(content_type: str, request: Request, payload: Any) -> SignalCommand:
    if content_type == "application/cloudevents+json":
        event_type = payload.get("type") if isinstance(payload, dict) else None
    else:
        event_type = request.headers.get("ce-type")
    _reject_unsupported_type(event_type)
    try:
        if content_type == "application/cloudevents+json":
            return structured_to_signal_command(
                StructuredCloudEvent.model_validate(payload),
                datetime.now(UTC),
            )
        return binary_to_signal_command(
            BinaryCloudEventContext.from_headers(request.headers),
            CloudEventData.model_validate(payload),
            datetime.now(UTC),
        )
    except ValidationError as error:
        raise ApiError(422, "validation_error", "请求字段不符合约束") from error


def _reject_unsupported_type(value: object) -> None:
    if value is not None and value != EVENT_TYPE:
        raise ApiError(422, "unsupported_event_type", "不支持该 CloudEvents 事件类型")
