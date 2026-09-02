from __future__ import annotations

from typing import Annotated, Never

from fastapi import APIRouter, Depends

from incident_intelligence.api.dependencies import (
    get_monitoring_data_source_service,
    require_manual_actor,
)
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.monitoring_data_sources import (
    CreateMonitoringDataSourceRequest,
    MonitoringConnectionTestResponse,
    MonitoringDataSourcePageResponse,
    MonitoringDataSourceResponse,
    UpdateMonitoringDataSourceRequest,
)
from incident_intelligence.services.monitoring_data_sources import (
    MonitoringDataSourceConflict,
    MonitoringDataSourceNotFound,
    MonitoringDataSourceService,
    MonitoringDataSourceVersionConflict,
)

router = APIRouter(
    prefix="/api/v1/monitoring-data-sources",
    tags=["monitoring-data-sources"],
)
ManualActor = Annotated[str, Depends(require_manual_actor)]
Service = Annotated[
    MonitoringDataSourceService,
    Depends(get_monitoring_data_source_service),
]
SOURCE_ERRORS = (
    MonitoringDataSourceConflict,
    MonitoringDataSourceNotFound,
    MonitoringDataSourceVersionConflict,
)
SourceError = (
    MonitoringDataSourceConflict
    | MonitoringDataSourceNotFound
    | MonitoringDataSourceVersionConflict
)


@router.get("", response_model=MonitoringDataSourcePageResponse)
def list_sources(actor: ManualActor, service: Service) -> MonitoringDataSourcePageResponse:
    del actor
    return MonitoringDataSourcePageResponse.model_validate(service.list())


@router.post("", response_model=MonitoringDataSourceResponse, status_code=201)
def create_source(
    request: CreateMonitoringDataSourceRequest,
    actor: ManualActor,
    service: Service,
) -> MonitoringDataSourceResponse:
    del actor
    try:
        return MonitoringDataSourceResponse.model_validate(service.create(**request.model_dump()))
    except SOURCE_ERRORS as error:
        _raise_source_error(error)


@router.patch("/{source_id}", response_model=MonitoringDataSourceResponse)
def update_source(
    source_id: str,
    request: UpdateMonitoringDataSourceRequest,
    actor: ManualActor,
    service: Service,
) -> MonitoringDataSourceResponse:
    del actor
    try:
        return MonitoringDataSourceResponse.model_validate(
            service.update(source_id, **request.model_dump())
        )
    except SOURCE_ERRORS as error:
        _raise_source_error(error)


@router.post("/{source_id}/test", response_model=MonitoringConnectionTestResponse)
def test_source(
    source_id: str,
    actor: ManualActor,
    service: Service,
) -> MonitoringConnectionTestResponse:
    del actor
    try:
        return MonitoringConnectionTestResponse.model_validate(service.test_connection(source_id))
    except SOURCE_ERRORS as error:
        _raise_source_error(error)


def _raise_source_error(error: SourceError) -> Never:
    if isinstance(error, MonitoringDataSourceNotFound):
        raise ApiError(404, error.reason_code, "未找到指定监控数据源") from error
    if isinstance(error, MonitoringDataSourceVersionConflict):
        raise ApiError(409, error.reason_code, "数据源已更新。请刷新后重试。") from error
    raise ApiError(
        409,
        error.reason_code,
        "同一环境的同类监控数据源只能启用一个",
    ) from error
