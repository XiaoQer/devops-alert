from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from incident_intelligence.adapters.common import AdapterValidationError
from incident_intelligence.domain.forbidden_identity import ForbiddenIdentityError
from incident_intelligence.services.signal_intake import SourceEventConflict


@dataclass(frozen=True, slots=True)
class ApiError(Exception):
    status_code: int
    code: str
    message: str


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"code": code, "message": message})


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, error: ApiError) -> JSONResponse:
        del request
        return error_response(error.status_code, error.code, error.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        del request
        forbidden = any(item.get("type") == "forbidden_identity" for item in error.errors())
        if forbidden:
            return error_response(422, "forbidden_identity", "请求包含平台禁止接收的字段")
        batch_too_large = any(
            item.get("type") == "too_long" and tuple(item.get("loc", ())) == ("body", "alerts")
            for item in error.errors()
        )
        if batch_too_large:
            return error_response(
                422,
                "batch_too_large",
                "Alertmanager 单批最多接收 100 条告警",
            )
        return error_response(422, "validation_error", "请求字段不符合约束")

    @app.exception_handler(AdapterValidationError)
    async def handle_adapter_validation_error(
        request: Request, error: AdapterValidationError
    ) -> JSONResponse:
        del request, error
        return error_response(422, "validation_error", "请求字段不符合约束")

    @app.exception_handler(ForbiddenIdentityError)
    async def handle_forbidden_identity(
        request: Request, error: ForbiddenIdentityError
    ) -> JSONResponse:
        del request, error
        return error_response(422, "forbidden_identity", "请求包含平台禁止接收的字段")

    @app.exception_handler(SourceEventConflict)
    async def handle_source_event_conflict(
        request: Request, error: SourceEventConflict
    ) -> JSONResponse:
        del request, error
        return error_response(409, "source_event_conflict", "来源事件身份与已有内容冲突")

    @app.exception_handler(SQLAlchemyError)
    async def handle_database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
        del request, error
        return error_response(503, "persistence_unavailable", "事故记录暂时无法保存。请稍后重试")
