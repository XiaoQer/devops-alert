from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from incident_intelligence.persistence.models import (
    IncidentActivityRow,
    IncidentOperationRow,
    IncidentRow,
)


class IncidentOperationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_operation(self, scope: str, idempotency_key_hash: str) -> IncidentOperationRow | None:
        return self._session.scalar(
            select(IncidentOperationRow).where(
                IncidentOperationRow.scope == scope,
                IncidentOperationRow.idempotency_key_hash == idempotency_key_hash,
            )
        )

    def lock_incident(self, incident_id: str) -> IncidentRow | None:
        return self._session.scalar(
            select(IncidentRow).where(IncidentRow.id == incident_id).with_for_update()
        )

    def add_activity(self, activity: IncidentActivityRow) -> None:
        self._session.add(activity)

    def add_operation(self, operation: IncidentOperationRow) -> None:
        self._session.add(operation)

    def flush(self) -> None:
        self._session.flush()
