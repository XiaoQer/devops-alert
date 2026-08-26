from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from incident_intelligence.adapters.alertmanager import (
    AlertmanagerWebhook,
    to_signal_commands,
)
from incident_intelligence.api.dependencies import (
    bearer_scheme,
    get_signal_intake_service,
    get_source_authentication_service,
    get_source_receipt_service,
    require_alertmanager_actor,
)
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.intake import (
    IntakeBatchResponse,
    IntakeValidationResponse,
)
from incident_intelligence.domain.alert_sources import ReceiptOutcome
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


@router.post(
    "/{alert_source_id}/validate",
    response_model=IntakeValidationResponse,
    status_code=202,
)
async def validate_registered_alertmanager(
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
    authenticated = _authenticate(
        alert_source_id,
        credentials,
        authentication,
        receipts,
        request_id,
        now,
    )
    webhook = await _validated_webhook(request, authenticated, receipts, request_id, now)
    receipts.record(
        _receipt_context(authenticated, request_id, now),
        outcome="VALIDATED",
        reason_code="source_payload_validated",
        input_count=len(webhook.alerts),
    )
    return IntakeValidationResponse(valid=True, input_count=len(webhook.alerts))


@router.post("/{alert_source_id}", response_model=IntakeBatchResponse, status_code=202)
async def receive_registered_alertmanager(
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
    authenticated = _authenticate(
        alert_source_id,
        credentials,
        authentication,
        receipts,
        request_id,
        now,
    )
    webhook = await _validated_webhook(request, authenticated, receipts, request_id, now)
    commands = to_signal_commands(
        webhook,
        now,
        alert_source_id=authenticated.alert_source_id,
    )
    try:
        result = service.submit_batch(
            commands,
            actor=authenticated.actor,
            request_id=request_id,
            receipt_context=_receipt_context(authenticated, request_id, now),
        )
    except (SourceEventConflict, SQLAlchemyError):
        _record_processing_failure(receipts, authenticated, request_id, now, len(commands))
        raise
    response.status_code = 200 if all(item.replayed for item in result.items) else 202
    return IntakeBatchResponse.from_result(result)


def _authenticate(
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
            expected_type="ALERTMANAGER",
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
                    adapter_type="ALERTMANAGER",
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


async def _validated_webhook(
    request: Request,
    authenticated: AuthenticatedAlertSource,
    receipts: SourceReceiptService,
    request_id: str,
    now: datetime,
) -> AlertmanagerWebhook:
    try:
        payload = await request.json()
        return AlertmanagerWebhook.model_validate(payload)
    except (ValueError, UnicodeDecodeError, ValidationError) as error:
        receipts.record(
            _receipt_context(authenticated, request_id, now),
            outcome="PAYLOAD_REJECTED",
            reason_code="invalid_source_payload",
            input_count=0,
        )
        raise ApiError(422, "invalid_source_payload", "告警内容不符合 Alertmanager 约束") from error


def _receipt_context(
    authenticated: AuthenticatedAlertSource,
    request_id: str,
    now: datetime,
) -> ReceiptContext:
    return ReceiptContext(
        alert_source_id=authenticated.alert_source_id,
        adapter_type="ALERTMANAGER",
        request_id=request_id,
        received_at=now,
    )


def _record_processing_failure(
    receipts: SourceReceiptService,
    authenticated: AuthenticatedAlertSource,
    request_id: str,
    now: datetime,
    input_count: int,
) -> None:
    try:
        receipts.record(
            _receipt_context(authenticated, request_id, now),
            outcome="PROCESSING_FAILED",
            reason_code="signal_processing_failed",
            input_count=input_count,
        )
    except SQLAlchemyError:
        return
