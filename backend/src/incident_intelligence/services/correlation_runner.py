from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from incident_intelligence.services.alert_group_correlation_jobs import (
    AlertGroupCorrelationLease,
)
from incident_intelligence.services.correlation_jobs import CorrelationJobLease
from incident_intelligence.settings import Settings


class JobService(Protocol):
    def claim_batch(
        self,
        lease_owner: str,
        now: datetime,
        *,
        limit: int,
        lease_seconds: int,
    ) -> tuple[CorrelationJobLease, ...]: ...

    def fail(
        self,
        job_id: str,
        lease_owner: str,
        error_code: str,
        now: datetime,
    ) -> str: ...


class CorrelationProcessor(Protocol):
    def process(self, lease: CorrelationJobLease) -> object: ...


class AlertGroupJobService(Protocol):
    def claim_batch(
        self,
        lease_owner: str,
        now: datetime,
        *,
        limit: int,
        lease_seconds: int,
    ) -> tuple[AlertGroupCorrelationLease, ...]: ...

    def fail(self, job_id: str, lease_owner: str, error_code: str, now: datetime) -> str: ...


class AlertGroupProcessor(Protocol):
    def process(self, lease: AlertGroupCorrelationLease) -> object: ...


class CorrelationRunner:
    def __init__(
        self,
        *,
        job_service: JobService,
        processor: CorrelationProcessor,
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
        self._lease_owner = f"correlation-runner-{uuid4().hex}"
        self.last_cycle_error_code: str | None = None

    async def run_once(self) -> int:
        now = self._clock().astimezone(UTC)
        leases = await asyncio.to_thread(
            self._job_service.claim_batch,
            self._lease_owner,
            now,
            limit=self._settings.correlation_batch_size,
            lease_seconds=self._settings.correlation_lease_seconds,
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
                        "correlation_processing_failed",
                        self._clock().astimezone(UTC),
                    )
                except Exception:
                    self.last_cycle_error_code = "correlation_cycle_failed"
        return len(leases)

    async def run_forever(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except Exception:
                self.last_cycle_error_code = "correlation_cycle_failed"
            await self._wait_for_next_cycle()

    async def stop(self) -> None:
        self._stop_event.set()

    async def _wait_for_next_cycle(self) -> None:
        if self._stop_event.is_set():
            return
        interval = self._settings.correlation_poll_interval_seconds
        if self._sleeper is not None:
            await self._sleeper(interval)
            return
        with suppress(TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=interval)


class AlertGroupCorrelationRunner:
    def __init__(
        self,
        *,
        job_service: AlertGroupJobService,
        processor: AlertGroupProcessor,
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
        self._lease_owner = f"group-correlation-runner-{uuid4().hex}"
        self.last_cycle_error_code: str | None = None

    async def run_once(self) -> int:
        now = self._clock().astimezone(UTC)
        leases: tuple[AlertGroupCorrelationLease, ...] = await asyncio.to_thread(
            self._job_service.claim_batch,
            self._lease_owner,
            now,
            limit=self._settings.correlation_batch_size,
            lease_seconds=self._settings.correlation_lease_seconds,
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
                        "group_correlation_processing_failed",
                        self._clock().astimezone(UTC),
                    )
                except Exception:
                    self.last_cycle_error_code = "group_correlation_cycle_failed"
        return len(leases)

    async def run_forever(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except Exception:
                self.last_cycle_error_code = "group_correlation_cycle_failed"
            await self._wait_for_next_cycle()

    async def stop(self) -> None:
        self._stop_event.set()

    async def _wait_for_next_cycle(self) -> None:
        if self._stop_event.is_set():
            return
        interval = self._settings.correlation_poll_interval_seconds
        if self._sleeper is not None:
            await self._sleeper(interval)
            return
        with suppress(TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
