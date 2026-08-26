from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.persistence.alert_group_repository import AlertGroupRepository
from incident_intelligence.persistence.models import AlertGroupingJobRow
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork


class AlertGroupingJobLease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^agj_[0-9a-f]{32}$")
    alert_id: str = Field(pattern=r"^alt_[0-9a-f]{32}$")
    alert_cycle: int = Field(ge=1)
    alert_version: int = Field(ge=1)
    state: str
    attempts: int = Field(ge=0, le=5)
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    last_error_code: str | None


@dataclass(frozen=True, slots=True)
class AlertGroupingJobNotRetryable(Exception):
    reason_code: str = "alert_grouping_job_not_retryable"


@dataclass(frozen=True, slots=True)
class AlertGroupingJobNotFound(Exception):
    reason_code: str = "resource_not_found"


class AlertGroupingJobService:
    def __init__(self, *, uow_factory: Callable[[], SqlAlchemyUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def claim_batch(
        self,
        lease_owner: str,
        now: datetime,
        *,
        limit: int,
        lease_seconds: int,
    ) -> tuple[AlertGroupingJobLease, ...]:
        if not 1 <= len(lease_owner) <= 128:
            raise ValueError("invalid_lease_owner")
        if not 1 <= limit <= 50 or not 5 <= lease_seconds <= 300:
            raise ValueError("alert_grouping_claim_out_of_range")
        with self._uow_factory() as uow:
            repository = _repository(uow)
            rows = repository.claimable_grouping_jobs(now=now, limit=limit)
            for row in rows:
                row.state = "LEASED"
                row.attempts += 1
                row.lease_owner = lease_owner
                row.lease_expires_at = now + timedelta(seconds=lease_seconds)
                row.last_error_code = None
                row.updated_at = now
            repository.flush()
            uow.commit()
            return tuple(_lease_from_row(row) for row in rows)

    def fail(
        self,
        job_id: str,
        lease_owner: str,
        error_code: str,
        now: datetime,
    ) -> str:
        if not 1 <= len(error_code) <= 64:
            raise ValueError("invalid_alert_grouping_error_code")
        with self._uow_factory() as uow:
            repository = _repository(uow)
            row = _owned_lease(repository, job_id, lease_owner)
            row.state = "FAILED" if row.attempts >= 5 else "PENDING"
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

    def retry_failed(self, job_id: str, now: datetime) -> AlertGroupingJobLease:
        with self._uow_factory() as uow:
            repository = _repository(uow)
            row = repository.find_grouping_job(job_id, for_update=True)
            if row is None:
                raise AlertGroupingJobNotFound()
            if row.state != "FAILED":
                raise AlertGroupingJobNotRetryable()
            row.state = "PENDING"
            row.attempts = 0
            row.available_at = now
            row.lease_owner = None
            row.lease_expires_at = None
            row.last_error_code = None
            row.updated_at = now
            repository.flush()
            uow.commit()
            return _lease_from_row(row)


def _repository(uow: SqlAlchemyUnitOfWork) -> AlertGroupRepository:
    if uow.alert_groups is None:
        raise RuntimeError("工作单元没有可用告警组仓储")
    return uow.alert_groups


def _owned_lease(
    repository: AlertGroupRepository,
    job_id: str,
    lease_owner: str,
) -> AlertGroupingJobRow:
    row = repository.find_grouping_job(job_id, for_update=True)
    if row is None or row.state != "LEASED" or row.lease_owner != lease_owner:
        raise AlertGroupingJobNotRetryable()
    return row


def _lease_from_row(row: AlertGroupingJobRow) -> AlertGroupingJobLease:
    return AlertGroupingJobLease.model_validate(
        {
            "id": row.id,
            "alert_id": row.alert_id,
            "alert_cycle": row.alert_cycle,
            "alert_version": row.alert_version,
            "state": row.state,
            "attempts": row.attempts,
            "available_at": row.available_at,
            "lease_owner": row.lease_owner,
            "lease_expires_at": row.lease_expires_at,
            "last_error_code": row.last_error_code,
        }
    )
