from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from incident_intelligence.ids import new_id
from incident_intelligence.persistence.models import CorrelationJobRow


class CorrelationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(
        self,
        *,
        alert_id: str,
        alert_version: int,
        now: datetime,
    ) -> CorrelationJobRow:
        row = CorrelationJobRow(
            id=new_id("cjob"),
            alert_id=alert_id,
            alert_version=alert_version,
            state="PENDING",
            attempts=0,
            available_at=now,
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=None,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def claimable_jobs(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[CorrelationJobRow, ...]:
        statement = (
            select(CorrelationJobRow)
            .where(
                or_(
                    and_(
                        CorrelationJobRow.state == "PENDING",
                        CorrelationJobRow.available_at <= now,
                    ),
                    and_(
                        CorrelationJobRow.state == "LEASED",
                        CorrelationJobRow.lease_expires_at <= now,
                    ),
                )
            )
            .order_by(
                CorrelationJobRow.available_at,
                CorrelationJobRow.created_at,
                CorrelationJobRow.id,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return tuple(self._session.scalars(statement))

    def find_job(
        self,
        job_id: str,
        *,
        for_update: bool = False,
    ) -> CorrelationJobRow | None:
        statement = select(CorrelationJobRow).where(CorrelationJobRow.id == job_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def flush(self) -> None:
        self._session.flush()
