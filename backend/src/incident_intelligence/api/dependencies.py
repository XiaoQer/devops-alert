from __future__ import annotations

from secrets import compare_digest
from typing import Annotated, cast

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import SecretStr

from incident_intelligence.api.errors import ApiError
from incident_intelligence.services.catalog import ServiceCatalogService
from incident_intelligence.services.correlation import CorrelationReadService
from incident_intelligence.services.correlation_jobs import CorrelationJobService
from incident_intelligence.services.manual_intake import ManualIntakeService
from incident_intelligence.services.signal_intake import SignalIntakeService
from incident_intelligence.settings import Settings

bearer_scheme = HTTPBearer(auto_error=False)


def _require_token(
    credentials: HTTPAuthorizationCredentials | None,
    expected: SecretStr,
    actor: str,
) -> str:
    supplied = credentials.credentials if credentials is not None else ""
    valid_scheme = credentials is not None and credentials.scheme.casefold() == "bearer"
    if not valid_scheme or not compare_digest(
        supplied.encode(), expected.get_secret_value().encode()
    ):
        raise ApiError(401, "authentication_required", "需要有效的访问凭证")
    return actor


def require_manual_actor(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> str:
    settings = cast(Settings, request.app.state.settings)
    return _require_token(credentials, settings.api_token, "manual-api-client")


def require_alertmanager_actor(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> str:
    settings = cast(Settings, request.app.state.settings)
    return _require_token(credentials, settings.alertmanager_token, "alertmanager-adapter")


def require_cloudevents_actor(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> str:
    settings = cast(Settings, request.app.state.settings)
    return _require_token(credentials, settings.cloudevents_token, "cloudevents-adapter")


def require_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    normalized = idempotency_key.strip() if idempotency_key is not None else ""
    if not 1 <= len(normalized) <= 256:
        raise ApiError(400, "invalid_idempotency_key", "需要长度为 1 到 256 的幂等键")
    return normalized


def get_manual_intake_service(request: Request) -> ManualIntakeService:
    return cast(ManualIntakeService, request.app.state.manual_intake_service)


def get_signal_intake_service(request: Request) -> SignalIntakeService:
    return cast(SignalIntakeService, request.app.state.signal_intake_service)


def get_catalog_service(request: Request) -> ServiceCatalogService:
    return cast(ServiceCatalogService, request.app.state.catalog_service)


def get_correlation_read_service(request: Request) -> CorrelationReadService:
    return cast(CorrelationReadService, request.app.state.correlation_read_service)


def get_correlation_job_service(request: Request) -> CorrelationJobService:
    return cast(CorrelationJobService, request.app.state.correlation_job_service)
