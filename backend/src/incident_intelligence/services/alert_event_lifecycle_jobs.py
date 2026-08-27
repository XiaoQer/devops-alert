from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.persistence.alert_group_repository import AlertGroupRepository
from incident_intelligence.persistence.models import AlertEventLifecycleJobRow
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork


class AlertEventLifecycleLease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^alj_[0-9a-f]{32}$")
    alert_group_id: str = Field(pattern=r"^agr_[0-9a-f]{32}$")
    target_group_version: int = Field(ge=1)
    action: str
    state: str
    attempts: int = Field(ge=0, le=5)
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    last_error_code: str | None


@dataclass(frozen=True, slots=True)
class AlertEventLifecycleJobNotRetryable(Exception):
    reason_code: str = "alert_event_lifecycle_job_not_retryable"


class AlertEventLifecycleJobService:
    def __init__(self, *, uow_factory: Callable[[], SqlAlchemyUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def claim_batch(
        self,
        lease_owner: str,
        now: datetime,
        *,
        limit: int,
        lease_seconds: int,
    ) -> tuple[AlertEventLifecycleLease, ...]:
        if not 1 <= len(lease_owner) <= 128:
            raise ValueError("invalid_lease_owner")
        if not 1 <= limit <= 50 or not 5 <= lease_seconds <= 300:
            raise ValueError("alert_event_lifecycle_claim_out_of_range")
        with self._uow_factory() as uow:
            repository = _repository(uow)
            rows = repository.claimable_lifecycle_jobs(now=now, limit=limit)
            for row in rows:
                row.state = "LEASED"
                row.attempts += 1
                row.lease_owner = lease_owner
                row.lease_expires_at = now + timedelta(seconds=lease_seconds)
                row.last_error_code = None
                row.updated_at = now
            repository.flush()
            uow.commit()
            return tuple(_lease(row) for row in rows)

    def fail(
        self,
        job_id: str,
        lease_owner: str,
        error_code: str,
        now: datetime,
    ) -> str:
        if not 1 <= len(error_code) <= 64:
            raise ValueError("invalid_alert_event_lifecycle_error_code")
        with self._uow_factory() as uow:
            repository = _repository(uow)
            row = _owned(repository, job_id, lease_owner)
            row.state = "FAILED" if row.attempts >= 5 else "PENDING"
            row.active_slot = None if row.state == "FAILED" else 1
            row.available_at = (
                now if row.state == "FAILED" else now + timedelta(seconds=min(300, 2**row.attempts))
            )
            row.lease_owner = None
            row.lease_expires_at = None
            row.last_error_code = error_code
            row.updated_at = now
            repository.flush()
            uow.commit()
            return row.state


def _repository(uow: SqlAlchemyUnitOfWork) -> AlertGroupRepository:
    if uow.alert_groups is None:
        raise RuntimeError("工作单元没有可用告警组仓储")
    return uow.alert_groups


def _owned(
    repository: AlertGroupRepository,
    job_id: str,
    lease_owner: str,
) -> AlertEventLifecycleJobRow:
    row = repository.find_lifecycle_job(job_id, for_update=True)
    if row is None or row.state != "LEASED" or row.lease_owner != lease_owner:
        raise AlertEventLifecycleJobNotRetryable()
    return row


def _lease(row: AlertEventLifecycleJobRow) -> AlertEventLifecycleLease:
    return AlertEventLifecycleLease.model_validate(
        {
            "id": row.id,
            "alert_group_id": row.alert_group_id,
            "target_group_version": row.target_group_version,
            "action": row.action,
            "state": row.state,
            "attempts": row.attempts,
            "available_at": row.available_at,
            "lease_owner": row.lease_owner,
            "lease_expires_at": row.lease_expires_at,
            "last_error_code": row.last_error_code,
        }
    )
