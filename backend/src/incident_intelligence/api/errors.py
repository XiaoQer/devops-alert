from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError


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
        return error_response(422, "validation_error", "请求字段不符合约束")

    @app.exception_handler(SQLAlchemyError)
    async def handle_database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
        del request, error
        return error_response(503, "persistence_unavailable", "事故记录暂时无法保存。请稍后重试")
