# ruff: noqa: RUF001

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.adapters.feishu import (
    FeishuMessageResult,
    FeishuPermanentError,
)
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.incident_repository import (
    IncidentFeishuThreadRecord,
    IncidentFeishuThreadRepository,
    IncidentNotificationRepository,
    IncidentNotificationRouteRepository,
    IncidentRepository,
)
from incident_intelligence.persistence.incident_rule_repository import IncidentRuleRepository
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork


class IncidentCardClient(Protocol):
    def send_incident_card(
        self,
        *,
        chat_id: str,
        card: dict[str, object],
    ) -> FeishuMessageResult: ...

    def update_incident_card(
        self,
        *,
        message_id: str,
        card: dict[str, object],
    ) -> None: ...

    def reply_to_thread(
        self,
        *,
        root_message_id: str,
        text: str,
    ) -> FeishuMessageResult: ...


class IncidentNotificationSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    incident_id: str = Field(pattern=r"^inc_[0-9a-f]{32}$")
    reference: str
    title: str
    state: str
    severity: str
    environment: str
    group_display_name: str
    alert_count: int = Field(ge=1)
    active_alert_count: int = Field(ge=0)
    rule_summary: str
    version: int = Field(ge=1)


class NotificationDeliveryResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    notification_id: str
    message_id: str | None
    skipped: bool = False


class IncidentNotificationService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        feishu_client: IncidentCardClient | None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[IdPrefix], str] = new_id,
    ) -> None:
        self._uow_factory = uow_factory
        self._feishu_client = feishu_client
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory

    def deliver(self, notification_id: str) -> NotificationDeliveryResult:
        now = self._clock().astimezone(UTC)
        with self._uow_factory() as uow:
            notifications = _notifications(uow)
            notification = notifications.get(notification_id)
            if notification is None:
                raise FeishuPermanentError("notification_not_found")
            incident = _incidents(uow).get(notification.incident_id)
            if incident is None:
                raise FeishuPermanentError("incident_not_found")
            route = _routes(uow).find_enabled(incident.environment)
            if route is None:
                return NotificationDeliveryResult(
                    notification_id=notification.id,
                    message_id=None,
                    skipped=True,
                )
            if self._feishu_client is None:
                raise FeishuPermanentError("feishu_credentials_unavailable")
            rule = _rules(uow).get(incident.incident_rule_id)
            if rule is None:
                raise FeishuPermanentError("incident_rule_not_found")
            card = render_incident_card(
                IncidentNotificationSnapshot(
                    incident_id=incident.id,
                    reference=incident.reference,
                    title=incident.title,
                    state=incident.state,
                    severity=incident.severity,
                    environment=incident.environment,
                    group_display_name=incident.group_display_name,
                    alert_count=incident.alert_count,
                    active_alert_count=incident.active_alert_count,
                    rule_summary=rule.summary,
                    version=incident.version,
                )
            )
            threads = _threads(uow)
            thread = threads.get_by_incident(incident.id)
            if thread is None:
                sent = self._feishu_client.send_incident_card(chat_id=route.chat_id, card=card)
                threads.insert(
                    IncidentFeishuThreadRecord(
                        id=self._id_factory("ift"),
                        incident_id=incident.id,
                        route_id=route.id,
                        chat_id=route.chat_id,
                        root_message_id=sent.message_id,
                        last_synced_at=now,
                        last_error_code=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
                uow.commit()
                return NotificationDeliveryResult(
                    notification_id=notification.id,
                    message_id=sent.message_id,
                )

            self._feishu_client.update_incident_card(
                message_id=thread.root_message_id,
                card=card,
            )
            activity_kind = notification.payload.get("activity_kind")
            if activity_kind in _THREAD_REPLY_ACTIVITY_KINDS:
                summary = notification.payload.get("activity_summary")
                if isinstance(summary, str) and summary.strip():
                    self._feishu_client.reply_to_thread(
                        root_message_id=thread.root_message_id,
                        text=summary.strip()[:500],
                    )
            return NotificationDeliveryResult(
                notification_id=notification.id,
                message_id=thread.root_message_id,
            )


_THREAD_REPLY_ACTIVITY_KINDS = frozenset(
    {
        "ACKNOWLEDGED",
        "RESOLVED",
        "SEVERITY_ESCALATED",
        "ALL_ALERTS_RECOVERED",
    }
)


def render_incident_card(snapshot: IncidentNotificationSnapshot) -> dict[str, object]:
    fields = (
        ("状态", snapshot.state),
        ("严重级别", snapshot.severity),
        ("环境", snapshot.environment),
        ("对象", snapshot.group_display_name),
        ("关联告警", f"{snapshot.alert_count} 条（活动 {snapshot.active_alert_count} 条）"),
        ("创建依据", snapshot.rule_summary),
    )
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": snapshot.reference},
            "subtitle": {"tag": "plain_text", "content": snapshot.title},
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": "\n".join(f"**{label}**：{value}" for label, value in fields),
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "查看 Incident"},
                            "type": "default",
                            "value": {"action": "VIEW", "incident_id": snapshot.incident_id},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "确认事故"},
                            "type": "primary",
                            "value": {
                                "action": "ACKNOWLEDGE",
                                "incident_id": snapshot.incident_id,
                                "expected_version": snapshot.version,
                            },
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "解决事故"},
                            "type": "default",
                            "value": {
                                "action": "RESOLVE",
                                "incident_id": snapshot.incident_id,
                                "expected_version": snapshot.version,
                            },
                        },
                    ],
                },
            ]
        },
    }


def _notifications(uow: SqlAlchemyUnitOfWork) -> IncidentNotificationRepository:
    if uow.incident_notifications is None:
        raise RuntimeError("工作单元没有可用通知仓储")
    return uow.incident_notifications


def _incidents(uow: SqlAlchemyUnitOfWork) -> IncidentRepository:
    if uow.incidents is None:
        raise RuntimeError("工作单元没有可用 Incident 仓储")
    return uow.incidents


def _routes(uow: SqlAlchemyUnitOfWork) -> IncidentNotificationRouteRepository:
    if uow.incident_notification_routes is None:
        raise RuntimeError("工作单元没有可用通知路由仓储")
    return uow.incident_notification_routes


def _rules(uow: SqlAlchemyUnitOfWork) -> IncidentRuleRepository:
    if uow.incident_rules is None:
        raise RuntimeError("工作单元没有可用 Incident 规则仓储")
    return uow.incident_rules


def _threads(uow: SqlAlchemyUnitOfWork) -> IncidentFeishuThreadRepository:
    if uow.incident_feishu_threads is None:
        raise RuntimeError("工作单元没有可用飞书会话仓储")
    return uow.incident_feishu_threads


__all__ = [
    "IncidentCardClient",
    "IncidentNotificationService",
    "IncidentNotificationSnapshot",
    "NotificationDeliveryResult",
    "render_incident_card",
]
