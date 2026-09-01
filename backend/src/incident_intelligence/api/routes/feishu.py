from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from incident_intelligence.api.dependencies import get_feishu_event_service
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.feishu import FeishuCallbackResponse
from incident_intelligence.services.feishu_events import (
    FeishuCallbackRejected,
    FeishuCallbackResult,
    FeishuEventService,
)

router = APIRouter(prefix="/api/v1/integrations/feishu", tags=["feishu"])
Service = Annotated[FeishuEventService, Depends(get_feishu_event_service)]


@router.post("/events", response_model=FeishuCallbackResponse)
async def receive_event(request: Request, service: Service) -> FeishuCallbackResponse:
    return await _handle(request, service.handle_event)


@router.post("/card-actions", response_model=FeishuCallbackResponse)
async def receive_card_action(request: Request, service: Service) -> FeishuCallbackResponse:
    return await _handle(request, service.handle_card_action)


async def _handle(
    request: Request,
    handler: Callable[[Mapping[str, str], bytes], FeishuCallbackResult],
) -> FeishuCallbackResponse:
    body = await request.body()
    try:
        result = handler(dict(request.headers), body)
    except FeishuCallbackRejected as error:
        raise ApiError(
            401,
            "invalid_feishu_callback",
            "飞书回调验证失败",
        ) from error
    return FeishuCallbackResponse.model_validate(result.model_dump())


__all__ = ["router"]
