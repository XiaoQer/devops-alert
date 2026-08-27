from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from incident_intelligence.services.alert_event_lifecycle_jobs import AlertEventLifecycleLease
from incident_intelligence.settings import Settings


class LifecycleJobService(Protocol):
    def claim_batch(
        self,
        lease_owner: str,
        now: datetime,
        *,
        limit: int,
        lease_seconds: int,
    ) -> tuple[AlertEventLifecycleLease, ...]: ...

    def fail(
        self,
        job_id: str,
        lease_owner: str,
        error_code: str,
        now: datetime,
    ) -> str: ...


class LifecycleProcessor(Protocol):
    def process(self, lease: AlertEventLifecycleLease) -> object: ...


class AlertEventLifecycleRunner:
    def __init__(
        self,
        *,
        job_service: LifecycleJobService,
        processor: LifecycleProcessor,
        settings: Settings,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._job_service = job_service
        self._processor = processor
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleeper = sleeper
        self._stop_event = asyncio.Event()
        self._lease_owner = f"alert-event-lifecycle-runner-{uuid4().hex}"
        self.last_cycle_error_code: str | None = None

    async def run_once(self) -> int:
        now = self._clock().astimezone(UTC)
        leases = await asyncio.to_thread(
            self._job_service.claim_batch,
            self._lease_owner,
            now,
            limit=self._settings.alert_event_lifecycle_batch_size,
            lease_seconds=self._settings.alert_event_lifecycle_lease_seconds,
        )
        self.last_cycle_error_code = None
        for lease in leases:
            try:
                await asyncio.to_thread(self._processor.process, lease)
            except Exception:
                try:
                    await asyncio.to_thread(
                        self._job_service.fail,
                        lease.id,
                        self._lease_owner,
                        "alert_event_lifecycle_processing_failed",
                        self._clock().astimezone(UTC),
                    )
                except Exception:
                    self.last_cycle_error_code = "alert_event_lifecycle_cycle_failed"
        return len(leases)

    async def run_forever(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except Exception:
                self.last_cycle_error_code = "alert_event_lifecycle_cycle_failed"
            await self._wait_for_next_cycle()

    async def stop(self) -> None:
        self._stop_event.set()

    async def _wait_for_next_cycle(self) -> None:
        if self._stop_event.is_set():
            return
        interval = self._settings.alert_event_lifecycle_poll_interval_seconds
        if self._sleeper is not None:
            await self._sleeper(interval)
            return
        with suppress(TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
