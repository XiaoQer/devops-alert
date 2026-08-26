# ruff: noqa: RUF001

from __future__ import annotations

from typing import Annotated, Never
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Response

from incident_intelligence.api.dependencies import (
    get_alert_source_service,
    require_idempotency_key,
    require_manual_actor,
)
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.alert_sources import (
    AlertSourceMutationResponse,
    AlertSourcePageResponse,
    AlertSourceReceiptPageResponse,
    AlertSourceResponse,
    CreateAlertSourceRequest,
    CredentialMutationRequest,
    UpdateAlertSourceRequest,
)
from incident_intelligence.domain.alert_sources import AlertSourceState, AlertSourceType
from incident_intelligence.services.alert_sources import (
    AlertSourceConflict,
    AlertSourceResourceNotFound,
    AlertSourceService,
    AlertSourceVersionConflict,
    CreateAlertSourceCommand,
    LastActiveCredentialError,
    SystemManagedSourceError,
    UpdateAlertSourceCommand,
)

router = APIRouter(prefix="/api/v1/alert-sources", tags=["alert-sources"])
ManualActor = Annotated[str, Depends(require_manual_actor)]
IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]
SourceService = Annotated[AlertSourceService, Depends(get_alert_source_service)]
SOURCE_ERRORS = (
    AlertSourceConflict,
    AlertSourceResourceNotFound,
    AlertSourceVersionConflict,
    LastActiveCredentialError,
    SystemManagedSourceError,
)
SourceError = (
    AlertSourceConflict
    | AlertSourceResourceNotFound
    | AlertSourceVersionConflict
    | LastActiveCredentialError
    | SystemManagedSourceError
)


@router.post("", response_model=AlertSourceMutationResponse, status_code=201)
def create_alert_source(
    request: CreateAlertSourceRequest,
    response: Response,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: SourceService,
) -> AlertSourceMutationResponse:
    try:
        result = service.create_source(
            CreateAlertSourceCommand(**request.model_dump()),
            idempotency_key=idempotency_key,
            actor=actor,
            request_id=_request_id(),
        )
    except SOURCE_ERRORS as error:
        _raise_source_error(error)
    if result.replayed:
        response.status_code = 200
    return AlertSourceMutationResponse.model_validate(result)


@router.get("", response_model=AlertSourcePageResponse)
def list_alert_sources(
    actor: ManualActor,
    service: SourceService,
    source_type: Annotated[AlertSourceType | None, Query()] = None,
    state: Annotated[AlertSourceState | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> AlertSourcePageResponse:
    del actor
    result = service.list_sources(
        source_type=source_type,
        state=state,
        limit=limit,
        offset=offset,
    )
    return AlertSourcePageResponse.model_validate(result)


@router.get("/{source_id}", response_model=AlertSourceResponse)
def get_alert_source(
    source_id: str,
    actor: ManualActor,
    service: SourceService,
) -> AlertSourceResponse:
    del actor
    try:
        result = service.get_source(source_id)
    except AlertSourceResourceNotFound as error:
        _raise_source_error(error)
    return AlertSourceResponse.model_validate(result)


@router.get("/{source_id}/receipts", response_model=AlertSourceReceiptPageResponse)
def list_alert_source_receipts(
    source_id: str,
    actor: ManualActor,
    service: SourceService,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> AlertSourceReceiptPageResponse:
    del actor
    try:
        result = service.list_receipts(source_id, limit=limit, offset=offset)
    except AlertSourceResourceNotFound as error:
        _raise_source_error(error)
    return AlertSourceReceiptPageResponse.model_validate(result)


@router.patch("/{source_id}", response_model=AlertSourceMutationResponse)
def update_alert_source(
    source_id: str,
    request: UpdateAlertSourceRequest,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: SourceService,
) -> AlertSourceMutationResponse:
    try:
        result = service.update_source(
            source_id,
            UpdateAlertSourceCommand(**request.model_dump()),
            idempotency_key=idempotency_key,
            actor=actor,
            request_id=_request_id(),
        )
    except SOURCE_ERRORS as error:
        _raise_source_error(error)
    return AlertSourceMutationResponse.model_validate(result)


@router.post("/{source_id}/credentials/rotate", response_model=AlertSourceMutationResponse)
def rotate_alert_source_credential(
    source_id: str,
    request: CredentialMutationRequest,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: SourceService,
) -> AlertSourceMutationResponse:
    try:
        result = service.rotate_credential(
            source_id,
            expected_version=request.expected_version,
            idempotency_key=idempotency_key,
            actor=actor,
            request_id=_request_id(),
        )
    except SOURCE_ERRORS as error:
        _raise_source_error(error)
    return AlertSourceMutationResponse.model_validate(result)


@router.post(
    "/{source_id}/credentials/{credential_id}/revoke",
    response_model=AlertSourceMutationResponse,
)
def revoke_alert_source_credential(
    source_id: str,
    credential_id: str,
    request: CredentialMutationRequest,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: SourceService,
) -> AlertSourceMutationResponse:
    try:
        result = service.revoke_credential(
            source_id,
            credential_id,
            expected_version=request.expected_version,
            idempotency_key=idempotency_key,
            actor=actor,
            request_id=_request_id(),
        )
    except SOURCE_ERRORS as error:
        _raise_source_error(error)
    return AlertSourceMutationResponse.model_validate(result)


def _request_id() -> str:
    return f"req_{uuid4().hex}"


def _raise_source_error(error: SourceError) -> Never:
    if isinstance(error, AlertSourceResourceNotFound):
        raise ApiError(404, "resource_not_found", "未找到指定告警源或凭据") from error
    if isinstance(error, AlertSourceVersionConflict):
        raise ApiError(
            409,
            "alert_source_version_conflict",
            "告警源版本已变化，请刷新后重试",
        ) from error
    if isinstance(error, SystemManagedSourceError):
        raise ApiError(409, error.reason_code, "系统兼容来源不可修改") from error
    if isinstance(error, LastActiveCredentialError):
        raise ApiError(409, error.reason_code, "启用的告警源必须保留有效凭据") from error
    raise ApiError(409, error.reason_code, "告警源操作冲突") from error
