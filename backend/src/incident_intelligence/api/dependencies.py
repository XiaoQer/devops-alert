from __future__ import annotations

from secrets import compare_digest
from typing import Annotated, cast

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from incident_intelligence.api.errors import ApiError
from incident_intelligence.services.manual_intake import ManualIntakeService
from incident_intelligence.settings import Settings

bearer_scheme = HTTPBearer(auto_error=False)


def require_actor(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> str:
    settings = cast(Settings, request.app.state.settings)
    expected = settings.api_token.get_secret_value()
    supplied = credentials.credentials if credentials is not None else ""
    valid_scheme = credentials is not None and credentials.scheme.casefold() == "bearer"
    if not valid_scheme or not compare_digest(supplied.encode(), expected.encode()):
        raise ApiError(401, "authentication_required", "需要有效的访问凭证")
    return "authenticated-api-client"


def require_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    normalized = idempotency_key.strip() if idempotency_key is not None else ""
    if not 1 <= len(normalized) <= 256:
        raise ApiError(400, "invalid_idempotency_key", "需要长度为 1 到 256 的幂等键")
    return normalized


def get_manual_intake_service(request: Request) -> ManualIntakeService:
    return cast(ManualIntakeService, request.app.state.manual_intake_service)
