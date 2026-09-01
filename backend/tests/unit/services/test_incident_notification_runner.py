from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from incident_intelligence.adapters.feishu import FeishuPermanentError, FeishuRetryableError
from incident_intelligence.persistence.incident_repository import IncidentNotificationRecord
from incident_intelligence.services.incident_notification_runner import (
    IncidentNotificationRunner,
)
from incident_intelligence.services.incident_notifications import NotificationDeliveryResult

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def test_retryable_failures_follow_bounded_backoff_then_fail_once() -> None:
    clock = MutableClock(NOW)
    notifications = FakeNotificationRepository(_notification())
    incidents = FakeIncidentRepository()
    runner = IncidentNotificationRunner(
        uow_factory=lambda: FakeUnitOfWork(notifications, incidents),
        processor=RetryableProcessor(),
        clock=clock,
        owner="worker-1",
        lease_seconds=30,
        max_attempts=5,
    )

    expected_delays = (5, 30, 120, 600)
    for expected_attempt, delay in enumerate(expected_delays, start=1):
        result = runner.run_once(limit=1)
        assert (result.retried, result.failed) == (1, 0)
        assert notifications.record.attempt_count == expected_attempt
        assert notifications.record.available_at == clock.current + timedelta(seconds=delay)
        clock.current = notifications.record.available_at

    final = runner.run_once(limit=1)

    assert (final.retried, final.failed) == (0, 1)
    assert notifications.record.attempt_count == 5
    assert notifications.record.state == "FAILED"
    assert len(incidents.activities) == 1
    assert incidents.activities[0].kind == "NOTIFICATION_FAILED"
    assert incidents.activities[0].metadata["notification_id"] == notifications.record.id


def test_permanent_failure_is_not_retried_and_audit_is_idempotent() -> None:
    notifications = FakeNotificationRepository(_notification())
    incidents = FakeIncidentRepository()
    runner = IncidentNotificationRunner(
        uow_factory=lambda: FakeUnitOfWork(notifications, incidents),
        processor=PermanentProcessor(),
        clock=lambda: NOW,
        owner="worker-1",
        lease_seconds=30,
        max_attempts=5,
    )

    first = runner.run_once(limit=1)
    notifications.record = replace(
        notifications.record,
        state="PENDING",
        attempt_count=0,
        available_at=NOW,
        last_error_code=None,
    )
    second = runner.run_once(limit=1)

    assert (first.retried, first.failed) == (0, 1)
    assert (second.retried, second.failed) == (0, 1)
    assert len(incidents.activities) == 1


def test_successful_skip_completes_without_message_id() -> None:
    notifications = FakeNotificationRepository(_notification())
    incidents = FakeIncidentRepository()
    runner = IncidentNotificationRunner(
        uow_factory=lambda: FakeUnitOfWork(notifications, incidents),
        processor=SkippedProcessor(),
        clock=lambda: NOW,
        owner="worker-1",
        lease_seconds=30,
        max_attempts=5,
    )

    result = runner.run_once(limit=1)

    assert (result.succeeded, result.skipped) == (1, 1)
    assert notifications.record.state == "SUCCEEDED"
    assert notifications.record.feishu_message_id is None


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class RetryableProcessor:
    def deliver(self, notification_id: str) -> NotificationDeliveryResult:
        del notification_id
        raise FeishuRetryableError("rate_limited")


class PermanentProcessor:
    def deliver(self, notification_id: str) -> NotificationDeliveryResult:
        del notification_id
        raise FeishuPermanentError("permission_denied")


class SkippedProcessor:
    def deliver(self, notification_id: str) -> NotificationDeliveryResult:
        return NotificationDeliveryResult(
            notification_id=notification_id,
            message_id=None,
            skipped=True,
        )


class FakeUnitOfWork:
    def __init__(
        self,
        notifications: FakeNotificationRepository,
        incidents: FakeIncidentRepository,
    ) -> None:
        self.incident_notifications = notifications
        self.incidents = incidents

    def __enter__(self) -> FakeUnitOfWork:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def commit(self) -> None:
        return None


class FakeNotificationRepository:
    def __init__(self, record: IncidentNotificationRecord) -> None:
        self.record = record

    def lease_due(
        self,
        *,
        owner: str,
        now: datetime,
        lease_until: datetime,
        limit: int,
    ) -> tuple[IncidentNotificationRecord, ...]:
        if limit < 1 or self.record.state != "PENDING" or self.record.available_at > now:
            return ()
        self.record = replace(
            self.record,
            state="LEASED",
            attempt_count=self.record.attempt_count + 1,
            lease_owner=owner,
            lease_expires_at=lease_until,
            updated_at=now,
        )
        return (self.record,)

    def succeed(
        self,
        notification_id: str,
        *,
        owner: str,
        feishu_message_id: str | None,
        now: datetime,
    ) -> bool:
        if not self._owned(notification_id, owner):
            return False
        self.record = replace(
            self.record,
            state="SUCCEEDED",
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=None,
            feishu_message_id=feishu_message_id,
            updated_at=now,
        )
        return True

    def retry(
        self,
        notification_id: str,
        *,
        owner: str,
        error_code: str,
        available_at: datetime,
        now: datetime,
    ) -> bool:
        if not self._owned(notification_id, owner):
            return False
        self.record = replace(
            self.record,
            state="PENDING",
            available_at=available_at,
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=error_code,
            updated_at=now,
        )
        return True

    def fail(
        self,
        notification_id: str,
        *,
        owner: str,
        error_code: str,
        now: datetime,
    ) -> bool:
        if not self._owned(notification_id, owner):
            return False
        self.record = replace(
            self.record,
            state="FAILED",
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=error_code,
            updated_at=now,
        )
        return True

    def _owned(self, notification_id: str, owner: str) -> bool:
        return (
            self.record.id == notification_id
            and self.record.state == "LEASED"
            and self.record.lease_owner == owner
        )


class FakeIncidentRepository:
    def __init__(self) -> None:
        self.activities = []

    def append_activities(self, activities) -> None:
        existing = {activity.id for activity in self.activities}
        self.activities.extend(activity for activity in activities if activity.id not in existing)


def _notification() -> IncidentNotificationRecord:
    return IncidentNotificationRecord(
        id="ino_11111111111111111111111111111111",
        incident_id="inc_22222222222222222222222222222222",
        activity_id="iact_33333333333333333333333333333333",
        notification_key="4" * 64,
        kind="CREATE_CARD",
        state="PENDING",
        payload={},
        attempt_count=0,
        available_at=NOW,
        lease_owner=None,
        lease_expires_at=None,
        last_error_code=None,
        feishu_message_id=None,
        created_at=NOW,
        updated_at=NOW,
    )
