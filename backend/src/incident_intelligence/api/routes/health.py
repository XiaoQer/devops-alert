from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


class LivenessResponse(BaseModel):
    status: Literal["alive"]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    database: Literal["available", "unavailable"]


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

    return router
