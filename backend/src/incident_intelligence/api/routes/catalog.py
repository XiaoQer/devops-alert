# ruff: noqa: RUF001

from __future__ import annotations

from typing import Annotated, Literal, Never
from uuid import uuid4

from fastapi import APIRouter, Depends, Query

from incident_intelligence.api.dependencies import (
    get_catalog_service,
    require_manual_actor,
)
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.catalog import (
    CreateDependencyRequest,
    CreateServiceRequest,
    ServiceCatalogListResponse,
    ServiceCatalogResponse,
    ServiceDependencyListResponse,
    ServiceDependencyResponse,
    UpdateDependencyRequest,
    UpdateServiceRequest,
)
from incident_intelligence.domain.enums import CatalogState, DependencyState
from incident_intelligence.services.catalog import (
    CatalogConflict,
    CatalogResourceNotFound,
    CatalogVersionConflict,
    CreateDependencyCommand,
    CreateServiceCommand,
    InvalidDependency,
    ServiceCatalogService,
    UpdateDependencyCommand,
    UpdateServiceCommand,
)

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])
ManualActor = Annotated[str, Depends(require_manual_actor)]
CatalogService = Annotated[ServiceCatalogService, Depends(get_catalog_service)]


@router.post("/services", response_model=ServiceCatalogResponse, status_code=201)
def create_service(
    request: CreateServiceRequest,
    actor: ManualActor,
    service: CatalogService,
) -> ServiceCatalogResponse:
    try:
        result = service.create_service(
            CreateServiceCommand(**request.model_dump()),
            actor=actor,
            request_id=_request_id(),
        )
    except (CatalogConflict, CatalogVersionConflict, CatalogResourceNotFound) as error:
        _raise_catalog_error(error)
    return ServiceCatalogResponse.model_validate(result)


@router.get("/services", response_model=ServiceCatalogListResponse)
def list_services(
    actor: ManualActor,
    service: CatalogService,
    state: Annotated[CatalogState | None, Query()] = None,
    environment: Annotated[
        Literal["production", "staging", "development", "unknown"] | None,
        Query(),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> ServiceCatalogListResponse:
    del actor
    items = service.list_services(
        state=state,
        environment=environment,
        limit=limit,
        offset=offset,
    )
    return ServiceCatalogListResponse(
        items=tuple(ServiceCatalogResponse.model_validate(item) for item in items),
        limit=limit,
        offset=offset,
    )


@router.get("/services/{service_id}", response_model=ServiceCatalogResponse)
def get_service(
    service_id: str,
    actor: ManualActor,
    service: CatalogService,
) -> ServiceCatalogResponse:
    del actor
    try:
        result = service.get_service(service_id)
    except CatalogResourceNotFound as error:
        _raise_catalog_error(error)
    return ServiceCatalogResponse.model_validate(result)


@router.patch("/services/{service_id}", response_model=ServiceCatalogResponse)
def update_service(
    service_id: str,
    request: UpdateServiceRequest,
    actor: ManualActor,
    service: CatalogService,
) -> ServiceCatalogResponse:
    try:
        result = service.update_service(
            service_id,
            UpdateServiceCommand(**request.model_dump()),
            actor=actor,
            request_id=_request_id(),
        )
    except (CatalogVersionConflict, CatalogResourceNotFound) as error:
        _raise_catalog_error(error)
    return ServiceCatalogResponse.model_validate(result)


@router.post("/dependencies", response_model=ServiceDependencyResponse, status_code=201)
def create_dependency(
    request: CreateDependencyRequest,
    actor: ManualActor,
    service: CatalogService,
) -> ServiceDependencyResponse:
    try:
        result = service.create_dependency(
            CreateDependencyCommand(**request.model_dump()),
            actor=actor,
            request_id=_request_id(),
        )
    except (
        CatalogConflict,
        CatalogResourceNotFound,
        InvalidDependency,
    ) as error:
        _raise_catalog_error(error)
    return ServiceDependencyResponse.model_validate(result)


@router.get("/dependencies", response_model=ServiceDependencyListResponse)
def list_dependencies(
    actor: ManualActor,
    service: CatalogService,
    service_id: Annotated[str | None, Query()] = None,
    state: Annotated[DependencyState | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> ServiceDependencyListResponse:
    del actor
    items = service.list_dependencies(
        service_id=service_id,
        state=state,
        limit=limit,
        offset=offset,
    )
    return ServiceDependencyListResponse(
        items=tuple(ServiceDependencyResponse.model_validate(item) for item in items),
        limit=limit,
        offset=offset,
    )


@router.patch("/dependencies/{dependency_id}", response_model=ServiceDependencyResponse)
def update_dependency(
    dependency_id: str,
    request: UpdateDependencyRequest,
    actor: ManualActor,
    service: CatalogService,
) -> ServiceDependencyResponse:
    try:
        result = service.update_dependency(
            dependency_id,
            UpdateDependencyCommand(**request.model_dump()),
            actor=actor,
            request_id=_request_id(),
        )
    except (
        CatalogVersionConflict,
        CatalogResourceNotFound,
        InvalidDependency,
    ) as error:
        _raise_catalog_error(error)
    return ServiceDependencyResponse.model_validate(result)


def _request_id() -> str:
    return f"req_{uuid4().hex}"


def _raise_catalog_error(
    error: CatalogConflict | CatalogVersionConflict | CatalogResourceNotFound | InvalidDependency,
) -> Never:
    if isinstance(error, CatalogConflict):
        raise ApiError(409, "catalog_conflict", "同环境服务或依赖关系已存在") from error
    if isinstance(error, CatalogVersionConflict):
        raise ApiError(
            409,
            "catalog_version_conflict",
            "资源版本已变化，请刷新后重试",
        ) from error
    if isinstance(error, InvalidDependency):
        raise ApiError(422, "invalid_dependency", "服务依赖关系不符合约束") from error
    raise ApiError(404, "resource_not_found", "未找到指定资源") from error
