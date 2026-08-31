# ruff: noqa: RUF001

from __future__ import annotations

from typing import Annotated, Never
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Response

from incident_intelligence.api.dependencies import (
    get_incident_rule_service,
    require_idempotency_key,
    require_manual_actor,
)
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.incident_rules import (
    CreateIncidentRuleRequest,
    IncidentRuleCopyRequest,
    IncidentRuleDeleteResponse,
    IncidentRuleDryRunRequest,
    IncidentRuleDryRunResponse,
    IncidentRuleMutationResponse,
    IncidentRulePageResponse,
    IncidentRuleResponse,
    IncidentRuleState,
    IncidentRuleVersionRequest,
    UpdateIncidentRuleRequest,
)
from incident_intelligence.services.incident_rules import (
    CreateIncidentRuleCommand,
    IncidentRuleConflict,
    IncidentRuleNotFound,
    IncidentRulePublishBlocked,
    IncidentRuleService,
    IncidentRuleVersionConflict,
)

router = APIRouter(prefix="/api/v1/incident-rules", tags=["incident-rules"])
ManualActor = Annotated[str, Depends(require_manual_actor)]
IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]
RuleService = Annotated[IncidentRuleService, Depends(get_incident_rule_service)]
RULE_ERRORS = (
    IncidentRuleNotFound,
    IncidentRuleConflict,
    IncidentRuleVersionConflict,
    IncidentRulePublishBlocked,
)
RuleError = (
    IncidentRuleNotFound
    | IncidentRuleConflict
    | IncidentRuleVersionConflict
    | IncidentRulePublishBlocked
)


@router.post("", response_model=IncidentRuleMutationResponse, status_code=201)
def create_incident_rule(
    request: CreateIncidentRuleRequest,
    response: Response,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: RuleService,
) -> IncidentRuleMutationResponse:
    try:
        result = service.create(
            CreateIncidentRuleCommand.model_validate(request.model_dump()),
            idempotency_key=idempotency_key,
            actor=actor,
            request_id=_request_id(),
        )
    except RULE_ERRORS as error:
        _raise_rule_error(error)
    if result.replayed:
        response.status_code = 200
    return IncidentRuleMutationResponse.model_validate(result)


@router.get("", response_model=IncidentRulePageResponse)
def list_incident_rules(
    actor: ManualActor,
    service: RuleService,
    state: Annotated[IncidentRuleState | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> IncidentRulePageResponse:
    del actor
    return IncidentRulePageResponse.model_validate(
        service.list(state=state, limit=limit, offset=offset)
    )


@router.get("/{rule_id}", response_model=IncidentRuleResponse)
def get_incident_rule(
    rule_id: str,
    actor: ManualActor,
    service: RuleService,
) -> IncidentRuleResponse:
    del actor
    try:
        return IncidentRuleResponse.model_validate(service.get(rule_id))
    except RULE_ERRORS as error:
        _raise_rule_error(error)


@router.patch("/{rule_id}", response_model=IncidentRuleMutationResponse)
def update_incident_rule(
    rule_id: str,
    request: UpdateIncidentRuleRequest,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: RuleService,
) -> IncidentRuleMutationResponse:
    try:
        result = service.update(
            rule_id,
            expected_version=request.expected_version,
            name=request.name,
            description=request.description,
            config=request.config,
            idempotency_key=idempotency_key,
            actor=actor,
            request_id=_request_id(),
        )
    except RULE_ERRORS as error:
        _raise_rule_error(error)
    return IncidentRuleMutationResponse.model_validate(result)


@router.delete("/{rule_id}", response_model=IncidentRuleDeleteResponse)
def delete_incident_rule(
    rule_id: str,
    request: IncidentRuleVersionRequest,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: RuleService,
) -> IncidentRuleDeleteResponse:
    try:
        result = service.delete(
            rule_id,
            expected_version=request.expected_version,
            idempotency_key=idempotency_key,
            actor=actor,
            request_id=_request_id(),
        )
    except RULE_ERRORS as error:
        _raise_rule_error(error)
    return IncidentRuleDeleteResponse.model_validate(result)


@router.post("/{rule_id}/dry-runs", response_model=IncidentRuleDryRunResponse)
def dry_run_incident_rule(
    rule_id: str,
    request: IncidentRuleDryRunRequest,
    actor: ManualActor,
    service: RuleService,
) -> IncidentRuleDryRunResponse:
    try:
        result = service.dry_run(
            rule_id,
            expected_version=request.expected_version,
            history_hours=request.history_hours,
            actor=actor,
        )
    except RULE_ERRORS as error:
        _raise_rule_error(error)
    return IncidentRuleDryRunResponse.model_validate(result)


@router.post("/{rule_id}/publish", response_model=IncidentRuleMutationResponse)
def publish_incident_rule(
    rule_id: str,
    request: IncidentRuleVersionRequest,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: RuleService,
) -> IncidentRuleMutationResponse:
    try:
        result = service.publish(
            rule_id,
            expected_version=request.expected_version,
            idempotency_key=idempotency_key,
            actor=actor,
            request_id=_request_id(),
        )
    except RULE_ERRORS as error:
        _raise_rule_error(error)
    return IncidentRuleMutationResponse.model_validate(result)


@router.post("/{rule_id}/disable", response_model=IncidentRuleMutationResponse)
def disable_incident_rule(
    rule_id: str,
    request: IncidentRuleVersionRequest,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: RuleService,
) -> IncidentRuleMutationResponse:
    try:
        result = service.disable(
            rule_id,
            expected_version=request.expected_version,
            idempotency_key=idempotency_key,
            actor=actor,
            request_id=_request_id(),
        )
    except RULE_ERRORS as error:
        _raise_rule_error(error)
    return IncidentRuleMutationResponse.model_validate(result)


@router.post("/{rule_id}/copies", response_model=IncidentRuleMutationResponse, status_code=201)
def copy_incident_rule(
    rule_id: str,
    request: IncidentRuleCopyRequest,
    response: Response,
    actor: ManualActor,
    idempotency_key: IdempotencyKey,
    service: RuleService,
) -> IncidentRuleMutationResponse:
    try:
        result = service.copy(
            rule_id,
            name=request.name,
            idempotency_key=idempotency_key,
            actor=actor,
            request_id=_request_id(),
        )
    except RULE_ERRORS as error:
        _raise_rule_error(error)
    if result.replayed:
        response.status_code = 200
    return IncidentRuleMutationResponse.model_validate(result)


def _request_id() -> str:
    return f"req_{uuid4().hex}"


def _raise_rule_error(error: RuleError) -> Never:
    if isinstance(error, IncidentRuleNotFound):
        raise ApiError(404, error.reason_code, "未找到指定 Incident 规则") from error
    if isinstance(error, IncidentRuleVersionConflict):
        raise ApiError(409, error.reason_code, "规则已被更新，请刷新后重试") from error
    if isinstance(error, IncidentRulePublishBlocked):
        raise ApiError(409, error.reason_code, "请先用当前规则完成一次未截断的试运行") from error
    raise ApiError(409, error.reason_code, "Incident 规则操作冲突") from error
