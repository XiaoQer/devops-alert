from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import case, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from incident_intelligence.persistence.models import (
    EvidenceCollectionTaskRow,
    IncidentEvaluationJobRow,
    IncidentNotificationOutboxRow,
    MonitoringDataSourceRow,
)


class LivenessResponse(BaseModel):
    status: Literal["alive"]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    database: Literal["available", "unavailable"]


class WorkerHealthSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pending_count: int
    leased_count: int
    failed_count: int
    oldest_pending_at: datetime | None
    last_success_at: datetime | None
    last_error_code: str | None


class FeishuHealthSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    configured: bool


class DifyHealthSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool
    configured: bool
    workflow_label: str | None
    missing_environment_keys: tuple[str, ...]


class MonitoringSourceHealthSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    configured: bool
    last_connection_state: str | None


class PlatformHealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ready", "not_ready"]
    database: Literal["available", "unavailable"]
    incident_evaluation: WorkerHealthSummary | None
    incident_notification: WorkerHealthSummary | None
    evidence_collection: WorkerHealthSummary | None
    monitoring_sources: dict[str, MonitoringSourceHealthSummary] | None
    feishu: FeishuHealthSummary
    dify: DifyHealthSummary


def create_health_router(engine: Engine) -> APIRouter:
    router = APIRouter(tags=["平台健康"])

    @router.get("/health/live", response_model=LivenessResponse)
    def liveness() -> LivenessResponse:
        return LivenessResponse(status="alive")

    @router.get("/health/ready", response_model=ReadinessResponse)
    def readiness(response: Response) -> ReadinessResponse:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except (OSError, SQLAlchemyError):
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return ReadinessResponse(status="not_ready", database="unavailable")
        return ReadinessResponse(status="ready", database="available")

    @router.get("/health", response_model=PlatformHealthResponse)
    def platform_health(request: Request, response: Response) -> PlatformHealthResponse:
        configured = bool(request.app.state.settings.feishu_capability().configured)
        dify_capability = request.app.state.settings.dify_capability()
        dify = DifyHealthSummary(**dify_capability.model_dump())
        try:
            evaluation = _worker_summary(engine, IncidentEvaluationJobRow)
            notification = _worker_summary(engine, IncidentNotificationOutboxRow)
            evidence = _worker_summary(engine, EvidenceCollectionTaskRow)
            monitoring_sources = _monitoring_source_summary(engine)
        except (OSError, SQLAlchemyError):
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return PlatformHealthResponse(
                status="not_ready",
                database="unavailable",
                incident_evaluation=None,
                incident_notification=None,
                evidence_collection=None,
                monitoring_sources=None,
                feishu=FeishuHealthSummary(configured=configured),
                dify=dify,
            )
        return PlatformHealthResponse(
            status="ready",
            database="available",
            incident_evaluation=evaluation,
            incident_notification=notification,
            evidence_collection=evidence,
            monitoring_sources=monitoring_sources,
            feishu=FeishuHealthSummary(configured=configured),
            dify=dify,
        )

    return router


def _worker_summary(
    engine: Engine,
    row: type[IncidentEvaluationJobRow]
    | type[IncidentNotificationOutboxRow]
    | type[EvidenceCollectionTaskRow],
) -> WorkerHealthSummary:
    with Session(engine) as session:
        counts = session.execute(
            select(
                func.coalesce(func.sum(case((row.state == "PENDING", 1), else_=0)), 0),
                func.coalesce(func.sum(case((row.state == "LEASED", 1), else_=0)), 0),
                func.coalesce(func.sum(case((row.state == "FAILED", 1), else_=0)), 0),
                func.min(case((row.state == "PENDING", row.created_at), else_=None)),
                func.max(case((row.state == "SUCCEEDED", row.updated_at), else_=None)),
            )
        ).one()
        last_error_code = session.scalar(
            select(row.last_error_code)
            .where(row.state == "FAILED")
            .order_by(row.updated_at.desc())
            .limit(1)
        )
    return WorkerHealthSummary(
        pending_count=int(counts[0]),
        leased_count=int(counts[1]),
        failed_count=int(counts[2]),
        oldest_pending_at=counts[3],
        last_success_at=counts[4],
        last_error_code=last_error_code,
    )


def _monitoring_source_summary(
    engine: Engine,
) -> dict[str, MonitoringSourceHealthSummary]:
    source_types = ("PROMETHEUS", "ELASTICSEARCH", "SKYWALKING")
    with Session(engine) as session:
        rows = tuple(
            session.scalars(
                select(MonitoringDataSourceRow)
                .where(MonitoringDataSourceRow.enabled.is_(True))
                .order_by(MonitoringDataSourceRow.updated_at.desc())
            )
        )
    return {
        source_type: MonitoringSourceHealthSummary(
            configured=bool(matches := [row for row in rows if row.source_type == source_type]),
            last_connection_state=(None if not matches else matches[0].last_test_state),
        )
        for source_type in source_types
    }
