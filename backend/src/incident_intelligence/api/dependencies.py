from __future__ import annotations

from secrets import compare_digest
from typing import Annotated, cast

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import SecretStr

from incident_intelligence.api.errors import ApiError
from incident_intelligence.services.alert_center import AlertCenterService
from incident_intelligence.services.alert_sources import AlertSourceService
from incident_intelligence.services.feishu_events import FeishuEventService
from incident_intelligence.services.incident_evidence import IncidentEvidenceService
from incident_intelligence.services.incident_notification_routes import (
    IncidentNotificationRouteService,
)
from incident_intelligence.services.incident_rules import IncidentRuleService
from incident_intelligence.services.incidents import IncidentService
from incident_intelligence.services.monitoring_data_sources import MonitoringDataSourceService
from incident_intelligence.services.signal_intake import SignalIntakeService
from incident_intelligence.services.source_authentication import SourceAuthenticationService
from incident_intelligence.services.source_receipts import SourceReceiptService
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


def get_alert_source_service(request: Request) -> AlertSourceService:
    return cast(AlertSourceService, request.app.state.alert_source_service)


def get_alert_center_service(request: Request) -> AlertCenterService:
    return cast(AlertCenterService, request.app.state.alert_center_service)


def get_incident_rule_service(request: Request) -> IncidentRuleService:
    return cast(IncidentRuleService, request.app.state.incident_rule_service)


def get_incident_notification_route_service(
    request: Request,
) -> IncidentNotificationRouteService:
    return cast(
        IncidentNotificationRouteService,
        request.app.state.incident_notification_route_service,
    )


def get_incident_service(request: Request) -> IncidentService:
    return cast(IncidentService, request.app.state.incident_service)


def get_incident_evidence_service(request: Request) -> IncidentEvidenceService:
    return cast(IncidentEvidenceService, request.app.state.incident_evidence_service)


def get_monitoring_data_source_service(request: Request) -> MonitoringDataSourceService:
    return cast(
        MonitoringDataSourceService,
        request.app.state.monitoring_data_source_service,
    )


def get_feishu_event_service(request: Request) -> FeishuEventService:
    service = cast(FeishuEventService | None, request.app.state.feishu_event_service)
    if service is None:
        raise ApiError(503, "feishu_not_configured", "飞书回调能力尚未配置")
    return service


def get_source_authentication_service(request: Request) -> SourceAuthenticationService:
    return cast(SourceAuthenticationService, request.app.state.source_authentication_service)


def get_source_receipt_service(request: Request) -> SourceReceiptService:
    return cast(SourceReceiptService, request.app.state.source_receipt_service)


def get_signal_intake_service(request: Request) -> SignalIntakeService:
    return cast(SignalIntakeService, request.app.state.signal_intake_service)
