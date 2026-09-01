from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from incident_intelligence.adapters.feishu import FeishuPermanentError, FeishuRetryableError
from incident_intelligence.domain.incidents import IncidentActivity
from incident_intelligence.persistence.incident_repository import (
    IncidentNotificationRecord,
    IncidentNotificationRepository,
    IncidentRepository,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.incident_notifications import NotificationDeliveryResult


class NotificationProcessor(Protocol):
    def deliver(self, notification_id: str) -> NotificationDeliveryResult: ...


class NotificationBatchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    leased: int = 0
    succeeded: int = 0
    skipped: int = 0
    retried: int = 0
    failed: int = 0


class IncidentNotificationRunner:
    RETRY_DELAYS_SECONDS = (5, 30, 120, 600, 1_800)

    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        processor: NotificationProcessor,
        clock: Callable[[], datetime] | None = None,
        owner: str = "incident-notification",
        lease_seconds: int = 60,
        max_attempts: int = 5,
    ) -> None:
        if not 1 <= max_attempts <= len(self.RETRY_DELAYS_SECONDS):
            raise ValueError("max_attempts 必须在 1 到 5 之间")
        self._uow_factory = uow_factory
        self._processor = processor
        self._clock = clock or (lambda: datetime.now(UTC))
        self._owner = owner
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts

    def run_once(self, *, limit: int) -> NotificationBatchResult:
        now = self._now()
        with self._uow_factory() as uow:
            leased = _notifications(uow).lease_due(
                owner=self._owner,
                now=now,
                lease_until=now + timedelta(seconds=self._lease_seconds),
                limit=limit,
            )
            uow.commit()

        succeeded = skipped = retried = failed = 0
        for notification in leased:
            try:
                delivery = self._processor.deliver(notification.id)
                self._complete(notification, delivery)
                succeeded += 1
                skipped += int(delivery.skipped)
            except FeishuPermanentError as error:
                self._fail(notification, error.error_code)
                failed += 1
            except FeishuRetryableError as error:
                if self._retry_or_fail(notification, error.error_code):
                    retried += 1
                else:
                    failed += 1
            except Exception as error:
                code = type(error).__name__.lower()[:64]
                if self._retry_or_fail(notification, code):
                    retried += 1
                else:
                    failed += 1
        return NotificationBatchResult(
            leased=len(leased),
            succeeded=succeeded,
            skipped=skipped,
            retried=retried,
            failed=failed,
        )

    def _complete(
        self,
        notification: IncidentNotificationRecord,
        delivery: NotificationDeliveryResult,
    ) -> None:
        with self._uow_factory() as uow:
            if not _notifications(uow).succeed(
                notification.id,
                owner=self._owner,
                feishu_message_id=delivery.message_id,
                now=self._now(),
            ):
                raise RuntimeError("notification_lease_lost")
            uow.commit()

    def _retry_or_fail(self, notification: IncidentNotificationRecord, error_code: str) -> bool:
        if notification.attempt_count >= self._max_attempts:
            self._fail(notification, error_code)
            return False
        now = self._now()
        delay = self.RETRY_DELAYS_SECONDS[notification.attempt_count - 1]
        with self._uow_factory() as uow:
            if not _notifications(uow).retry(
                notification.id,
                owner=self._owner,
                error_code=_bounded_error_code(error_code),
                available_at=now + timedelta(seconds=delay),
                now=now,
            ):
                raise RuntimeError("notification_lease_lost")
            uow.commit()
        return True

    def _fail(self, notification: IncidentNotificationRecord, error_code: str) -> None:
        now = self._now()
        bounded_code = _bounded_error_code(error_code)
        with self._uow_factory() as uow:
            if not _notifications(uow).fail(
                notification.id,
                owner=self._owner,
                error_code=bounded_code,
                now=now,
            ):
                raise RuntimeError("notification_lease_lost")
            activity_id = (
                "iact_"
                + sha256(f"{notification.id}\x1fNOTIFICATION_FAILED".encode()).hexdigest()[:32]
            )
            _incidents(uow).append_activities(
                (
                    IncidentActivity(
                        id=activity_id,
                        incident_id=notification.incident_id,
                        kind="NOTIFICATION_FAILED",
                        occurred_at=now,
                        actor_type="SYSTEM",
                        actor="incident-notification",
                        summary="Incident 飞书通知发送失败, 已停止自动重试",
                        metadata={
                            "notification_id": notification.id,
                            "error_code": bounded_code,
                        },
                    ),
                )
            )
            uow.commit()

    def _now(self) -> datetime:
        return self._clock().astimezone(UTC)


def _bounded_error_code(error_code: str) -> str:
    normalized = error_code.strip().lower().replace(" ", "_")
    return (normalized or "unknown_error")[:64]


def _notifications(uow: SqlAlchemyUnitOfWork) -> IncidentNotificationRepository:
    if uow.incident_notifications is None:
        raise RuntimeError("工作单元没有可用通知仓储")
    return uow.incident_notifications


def _incidents(uow: SqlAlchemyUnitOfWork) -> IncidentRepository:
    if uow.incidents is None:
        raise RuntimeError("工作单元没有可用 Incident 仓储")
    return uow.incidents


__all__ = ["IncidentNotificationRunner", "NotificationBatchResult"]
