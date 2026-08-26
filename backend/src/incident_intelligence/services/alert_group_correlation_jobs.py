from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.persistence.alert_group_repository import AlertGroupRepository
from incident_intelligence.persistence.models import AlertGroupCorrelationJobRow
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork


class AlertGroupCorrelationLease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str = Field(pattern=r"^gcj_[0-9a-f]{32}$")
    alert_group_id: str = Field(pattern=r"^agr_[0-9a-f]{32}$")
    target_group_version: int = Field(ge=1)
    state: str
    attempts: int = Field(ge=0, le=5)
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    last_error_code: str | None


@dataclass(frozen=True, slots=True)
class AlertGroupCorrelationJobNotRetryable(Exception):
    reason_code: str = "alert_group_correlation_job_not_retryable"


class AlertGroupCorrelationJobService:
    def __init__(self, *, uow_factory: Callable[[], SqlAlchemyUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def schedule(self, group_id: str, target_version: int, now: datetime) -> str:
        with self._uow_factory() as uow:
            repository = _repository(uow)
            group = repository.find_group(group_id, for_update=True)
            if group is None:
                raise ValueError("alert_group_not_found")
            row = repository.schedule_correlation(group, target_version=target_version, now=now)
            uow.commit()
            return "DEFERRED" if row is not None and row.state == "LEASED" else "SCHEDULED"

    def claim_batch(
        self, lease_owner: str, now: datetime, *, limit: int, lease_seconds: int
    ) -> tuple[AlertGroupCorrelationLease, ...]:
        if not 1 <= len(lease_owner) <= 128:
            raise ValueError("invalid_lease_owner")
        if not 1 <= limit <= 50 or not 5 <= lease_seconds <= 300:
            raise ValueError("group_correlation_claim_out_of_range")
        with self._uow_factory() as uow:
            repository = _repository(uow)
            rows = repository.claimable_correlation_jobs(now=now, limit=limit)
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

    def fail(self, job_id: str, lease_owner: str, error_code: str, now: datetime) -> str:
        if not 1 <= len(error_code) <= 64:
            raise ValueError("invalid_group_correlation_error_code")
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
            if row.state == "FAILED":
                group = repository.find_group(row.alert_group_id, for_update=True)
                if (
                    group is not None
                    and group.desired_correlation_version > row.target_group_version
                ):
                    repository.schedule_correlation(
                        group,
                        target_version=group.desired_correlation_version,
                        now=now,
                    )
            uow.commit()
            return row.state

    def retry_failed(self, job_id: str, now: datetime) -> AlertGroupCorrelationLease:
        with self._uow_factory() as uow:
            repository = _repository(uow)
            row = repository.find_correlation_job(job_id, for_update=True)
            if row is None or row.state != "FAILED":
                raise AlertGroupCorrelationJobNotRetryable()
            if repository.find_active_correlation_job(row.alert_group_id, for_update=True):
                raise AlertGroupCorrelationJobNotRetryable()
            group = repository.find_group(row.alert_group_id, for_update=True)
            if group is None:
                raise AlertGroupCorrelationJobNotRetryable()
            row.state = "PENDING"
            row.active_slot = 1
            row.target_group_version = max(
                row.target_group_version, group.desired_correlation_version
            )
            row.attempts = 0
            row.available_at = now
            row.lease_owner = None
            row.lease_expires_at = None
            row.last_error_code = None
            row.updated_at = now
            repository.flush()
            uow.commit()
            return _lease(row)


def _repository(uow: SqlAlchemyUnitOfWork) -> AlertGroupRepository:
    if uow.alert_groups is None:
        raise RuntimeError("工作单元没有可用告警组仓储")
    return uow.alert_groups


def _owned(
    repository: AlertGroupRepository, job_id: str, lease_owner: str
) -> AlertGroupCorrelationJobRow:
    row = repository.find_correlation_job(job_id, for_update=True)
    if row is None or row.state != "LEASED" or row.lease_owner != lease_owner:
        raise AlertGroupCorrelationJobNotRetryable()
    return row


def _lease(row: AlertGroupCorrelationJobRow) -> AlertGroupCorrelationLease:
    return AlertGroupCorrelationLease.model_validate(
        {
            "id": row.id,
            "alert_group_id": row.alert_group_id,
            "target_group_version": row.target_group_version,
            "state": row.state,
            "attempts": row.attempts,
            "available_at": row.available_at,
            "lease_owner": row.lease_owner,
            "lease_expires_at": row.lease_expires_at,
            "last_error_code": row.last_error_code,
        }
    )
