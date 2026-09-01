from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from incident_intelligence.domain.models import Environment, UtcAwareDatetime

FeishuChatId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
FeishuChatName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]


class IncidentNotificationRoute(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^inr_[0-9a-f]{32}$")
    environment: Environment
    chat_id: FeishuChatId
    chat_name: FeishuChatName
    enabled: bool
    enabled_environment_key: Environment | None
    version: int = Field(ge=1)
    created_at: UtcAwareDatetime
    updated_at: UtcAwareDatetime

    @model_validator(mode="after")
    def validate_enabled_key(self) -> IncidentNotificationRoute:
        expected = self.environment if self.enabled else None
        if self.enabled_environment_key != expected:
            raise ValueError("enabled_environment_key 与启用状态不一致")
        return self


def create_notification_route(
    *,
    route_id: str,
    environment: str,
    chat_id: str,
    chat_name: str,
    enabled: bool,
    now: UtcAwareDatetime,
) -> IncidentNotificationRoute:
    return IncidentNotificationRoute(
        id=route_id,
        environment=environment,
        chat_id=chat_id,
        chat_name=chat_name,
        enabled=enabled,
        enabled_environment_key=environment if enabled else None,
        version=1,
        created_at=now,
        updated_at=now,
    )


def update_notification_route(
    route: IncidentNotificationRoute,
    *,
    environment: str,
    chat_id: str,
    chat_name: str,
    enabled: bool,
    now: UtcAwareDatetime,
) -> IncidentNotificationRoute:
    return IncidentNotificationRoute.model_validate(
        route.model_copy(
            update={
                "environment": environment,
                "chat_id": chat_id,
                "chat_name": chat_name,
                "enabled": enabled,
                "enabled_environment_key": environment if enabled else None,
                "version": route.version + 1,
                "updated_at": now,
            }
        )
    )


__all__ = [
    "FeishuChatId",
    "FeishuChatName",
    "IncidentNotificationRoute",
    "create_notification_route",
    "update_notification_route",
]
