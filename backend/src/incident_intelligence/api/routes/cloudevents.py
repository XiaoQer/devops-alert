from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from incident_intelligence.adapters.cloudevents import (
    EVENT_TYPE,
    BinaryCloudEventContext,
    CloudEventData,
    StructuredCloudEvent,
    binary_to_signal_command,
    structured_to_signal_command,
)
from incident_intelligence.api.dependencies import (
    bearer_scheme,
    get_signal_intake_service,
    get_source_authentication_service,
    get_source_receipt_service,
    require_cloudevents_actor,
)
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.intake import (
    IntakeBatchResponse,
    IntakeValidationResponse,
)
from incident_intelligence.domain.alert_sources import (
    CLOUDEVENTS_COMPAT_SOURCE_ID,
    ReceiptOutcome,
)
from incident_intelligence.domain.signal_intake import SignalCommand
from incident_intelligence.services.signal_intake import SignalIntakeService, SourceEventConflict
from incident_intelligence.services.source_authentication import (
    AuthenticatedAlertSource,
    SourceAuthenticationError,
    SourceAuthenticationService,
)
from incident_intelligence.services.source_receipts import (
    ReceiptContext,
    SourceReceiptService,
)

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


def _to_command(
    content_type: str,
    request: Request,
    payload: Any,
    *,
    alert_source_id: str = CLOUDEVENTS_COMPAT_SOURCE_ID,
) -> SignalCommand:
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
                alert_source_id=alert_source_id,
            )
        return binary_to_signal_command(
            BinaryCloudEventContext.from_headers(request.headers),
            CloudEventData.model_validate(payload),
            datetime.now(UTC),
            alert_source_id=alert_source_id,
        )
    except ValidationError as error:
        raise ApiError(422, "validation_error", "请求字段不符合约束") from error


def _reject_unsupported_type(value: object) -> None:
    if value is not None and value != EVENT_TYPE:
        raise ApiError(422, "unsupported_event_type", "不支持该 CloudEvents 事件类型")


@router.post(
    "/{alert_source_id}/validate",
    response_model=IntakeValidationResponse,
    status_code=202,
)
async def validate_registered_cloudevent(
    alert_source_id: str,
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    authentication: Annotated[
        SourceAuthenticationService,
        Depends(get_source_authentication_service),
    ],
    receipts: Annotated[SourceReceiptService, Depends(get_source_receipt_service)],
) -> IntakeValidationResponse:
    now = datetime.now(UTC)
    request_id = f"req_{uuid4().hex}"
    authenticated = _authenticate_registered(
        alert_source_id,
        credentials,
        authentication,
        receipts,
        request_id,
        now,
    )
    await _validated_registered_command(request, authenticated, receipts, request_id, now)
    receipts.record(
        _receipt_context(authenticated, request_id, now),
        outcome="VALIDATED",
        reason_code="source_payload_validated",
        input_count=1,
    )
    return IntakeValidationResponse(valid=True, input_count=1)


@router.post("/{alert_source_id}", response_model=IntakeBatchResponse, status_code=202)
async def receive_registered_cloudevent(
    alert_source_id: str,
    request: Request,
    response: Response,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    authentication: Annotated[
        SourceAuthenticationService,
        Depends(get_source_authentication_service),
    ],
    receipts: Annotated[SourceReceiptService, Depends(get_source_receipt_service)],
    service: Annotated[SignalIntakeService, Depends(get_signal_intake_service)],
) -> IntakeBatchResponse:
    now = datetime.now(UTC)
    request_id = f"req_{uuid4().hex}"
    authenticated = _authenticate_registered(
        alert_source_id,
        credentials,
        authentication,
        receipts,
        request_id,
        now,
    )
    command = await _validated_registered_command(
        request,
        authenticated,
        receipts,
        request_id,
        now,
    )
    try:
        result = service.submit_batch(
            (command,),
            actor=authenticated.actor,
            request_id=request_id,
            receipt_context=_receipt_context(authenticated, request_id, now),
        )
    except (SourceEventConflict, SQLAlchemyError):
        _record_processing_failure(receipts, authenticated, request_id, now)
        raise
    response.status_code = 200 if result.items[0].replayed else 202
    return IntakeBatchResponse.from_result(result)


def _authenticate_registered(
    alert_source_id: str,
    credentials: HTTPAuthorizationCredentials | None,
    authentication: SourceAuthenticationService,
    receipts: SourceReceiptService,
    request_id: str,
    now: datetime,
) -> AuthenticatedAlertSource:
    token = credentials.credentials if credentials is not None else ""
    if credentials is None or credentials.scheme.casefold() != "bearer":
        token = ""
    try:
        return authentication.authenticate(
            alert_source_id,
            expected_type="CLOUDEVENTS",
            bearer_token=token,
            now=now,
        )
    except SourceAuthenticationError as error:
        if error.authenticated_source_id is not None:
            outcome: ReceiptOutcome = (
                "SOURCE_DISABLED"
                if error.reason_code == "alert_source_disabled"
                else "PAYLOAD_REJECTED"
            )
            receipts.record(
                ReceiptContext(
                    alert_source_id=error.authenticated_source_id,
                    adapter_type="CLOUDEVENTS",
                    request_id=request_id,
                    received_at=now,
                ),
                outcome=outcome,
                reason_code=error.reason_code,
                input_count=0,
            )
        if error.reason_code == "authentication_required":
            raise ApiError(401, "authentication_required", "需要有效的来源访问凭证") from error
        raise ApiError(409, error.reason_code, "告警源当前不能接收该类型数据") from error


async def _validated_registered_command(
    request: Request,
    authenticated: AuthenticatedAlertSource,
    receipts: SourceReceiptService,
    request_id: str,
    now: datetime,
) -> SignalCommand:
    try:
        content_type = request.headers.get("content-type", "").partition(";")[0].strip().casefold()
        if content_type not in {"application/cloudevents+json", "application/json"}:
            raise ValueError("unsupported media type")
        payload = await request.json()
        return _to_command(
            content_type,
            request,
            payload,
            alert_source_id=authenticated.alert_source_id,
        )
    except (ApiError, ValueError, UnicodeDecodeError, ValidationError) as error:
        receipts.record(
            _receipt_context(authenticated, request_id, now),
            outcome="PAYLOAD_REJECTED",
            reason_code="invalid_source_payload",
            input_count=0,
        )
        raise ApiError(422, "invalid_source_payload", "CloudEvents 内容不符合约束") from error


def _receipt_context(
    authenticated: AuthenticatedAlertSource,
    request_id: str,
    now: datetime,
) -> ReceiptContext:
    return ReceiptContext(
        alert_source_id=authenticated.alert_source_id,
        adapter_type="CLOUDEVENTS",
        request_id=request_id,
        received_at=now,
    )


def _record_processing_failure(
    receipts: SourceReceiptService,
    authenticated: AuthenticatedAlertSource,
    request_id: str,
    now: datetime,
) -> None:
    try:
        receipts.record(
            _receipt_context(authenticated, request_id, now),
            outcome="PROCESSING_FAILED",
            reason_code="signal_processing_failed",
            input_count=1,
        )
    except SQLAlchemyError:
        return
