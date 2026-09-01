from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.domain.incident_notifications import (
    FeishuChatId,
    FeishuChatName,
)
from incident_intelligence.domain.models import Environment
from incident_intelligence.services.incident_notification_routes import (
    IncidentNotificationRouteMutationResult,
    IncidentNotificationRoutePage,
)


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateIncidentNotificationRouteRequest(_Request):
    environment: Environment
    chat_id: FeishuChatId
    chat_name: FeishuChatName
    enabled: bool


class UpdateIncidentNotificationRouteRequest(CreateIncidentNotificationRouteRequest):
    expected_version: int = Field(ge=1)


class IncidentNotificationRoutePageResponse(IncidentNotificationRoutePage):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


class IncidentNotificationRouteMutationResponse(IncidentNotificationRouteMutationResult):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


__all__ = [
    "CreateIncidentNotificationRouteRequest",
    "IncidentNotificationRouteMutationResponse",
    "IncidentNotificationRoutePageResponse",
    "UpdateIncidentNotificationRouteRequest",
]
