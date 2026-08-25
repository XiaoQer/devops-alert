from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.persistence.correlation_repository import CorrelationRepository
from incident_intelligence.persistence.models import CorrelationJobRow
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork


class CorrelationJobLease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^cjob_[0-9a-f]{32}$")
    alert_id: str = Field(pattern=r"^alt_[0-9a-f]{32}$")
    alert_version: int = Field(ge=1)
    state: str
    attempts: int = Field(ge=0, le=5)
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    last_error_code: str | None


@dataclass(frozen=True, slots=True)
class CorrelationJobNotRetryable(Exception):
    reason_code: str = "correlation_job_not_retryable"


class CorrelationJobService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    ) -> None:
        self._uow_factory = uow_factory

    def claim_batch(
        self,
        lease_owner: str,
        now: datetime,
        *,
        limit: int,
        lease_seconds: int,
    ) -> tuple[CorrelationJobLease, ...]:
        if not 1 <= len(lease_owner) <= 128:
            raise ValueError("invalid_lease_owner")
        if not 1 <= limit <= 50 or not 5 <= lease_seconds <= 300:
            raise ValueError("correlation_claim_out_of_range")
        with self._uow_factory() as uow:
            repository = _correlation(uow)
            rows = repository.claimable_jobs(now=now, limit=limit)
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

    def complete(self, job_id: str, lease_owner: str, now: datetime) -> str:
        with self._uow_factory() as uow:
            repository = _correlation(uow)
            row = _owned_lease(repository, job_id, lease_owner)
            row.state = "SUCCEEDED"
            row.lease_owner = None
            row.lease_expires_at = None
            row.last_error_code = None
            row.updated_at = now
            repository.flush()
            uow.commit()
            return row.state

    def fail(
        self,
        job_id: str,
        lease_owner: str,
        error_code: str,
        now: datetime,
    ) -> str:
        if not 1 <= len(error_code) <= 64:
            raise ValueError("invalid_correlation_error_code")
        with self._uow_factory() as uow:
            repository = _correlation(uow)
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

    def retry_failed(self, job_id: str, now: datetime) -> CorrelationJobLease:
        with self._uow_factory() as uow:
            repository = _correlation(uow)
            row = repository.find_job(job_id, for_update=True)
            if row is None or row.state != "FAILED":
                raise CorrelationJobNotRetryable()
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


def _correlation(uow: SqlAlchemyUnitOfWork) -> CorrelationRepository:
    if uow.correlation is None:
        raise RuntimeError("工作单元没有可用关联仓储")
    return uow.correlation


def _owned_lease(
    repository: CorrelationRepository,
    job_id: str,
    lease_owner: str,
) -> CorrelationJobRow:
    row = repository.find_job(job_id, for_update=True)
    if row is None or row.state != "LEASED" or row.lease_owner != lease_owner:
        raise CorrelationJobNotRetryable()
    return row


def _lease_from_row(row: CorrelationJobRow) -> CorrelationJobLease:
    return CorrelationJobLease.model_validate(
        {
            "id": row.id,
            "alert_id": row.alert_id,
            "alert_version": row.alert_version,
            "state": row.state,
            "attempts": row.attempts,
            "available_at": row.available_at,
            "lease_owner": row.lease_owner,
            "lease_expires_at": row.lease_expires_at,
            "last_error_code": row.last_error_code,
        }
    )
