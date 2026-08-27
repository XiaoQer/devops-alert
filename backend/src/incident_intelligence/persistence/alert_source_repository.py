from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from incident_intelligence.persistence.models import (
    AlertRow,
    AlertSourceCredentialRow,
    AlertSourceOperationRow,
    AlertSourceReceiptRow,
    AlertSourceRow,
)


class AlertSourceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_source(
        self,
        source_id: str,
        *,
        for_update: bool = False,
    ) -> AlertSourceRow | None:
        statement = select(AlertSourceRow).where(AlertSourceRow.id == source_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def find_source_by_name(self, name: str) -> AlertSourceRow | None:
        return self._session.scalar(select(AlertSourceRow).where(AlertSourceRow.name == name))

    def list_sources(
        self,
        *,
        source_type: str | None,
        state: str | None,
        limit: int,
        offset: int,
    ) -> tuple[AlertSourceRow, ...]:
        statement = select(AlertSourceRow)
        if source_type is not None:
            statement = statement.where(AlertSourceRow.source_type == source_type)
        if state is not None:
            statement = statement.where(AlertSourceRow.state == state)
        statement = (
            statement.order_by(AlertSourceRow.created_at, AlertSourceRow.id)
            .limit(limit)
            .offset(offset)
        )
        return tuple(self._session.scalars(statement))

    def count_sources(self, *, source_type: str | None, state: str | None) -> int:
        statement = select(func.count()).select_from(AlertSourceRow)
        if source_type is not None:
            statement = statement.where(AlertSourceRow.source_type == source_type)
        if state is not None:
            statement = statement.where(AlertSourceRow.state == state)
        return self._session.scalar(statement) or 0

    def add_source(self, row: AlertSourceRow) -> None:
        self._session.add(row)
        self._session.flush()

    def find_credential(
        self,
        source_id: str,
        credential_id: str,
        *,
        for_update: bool = False,
    ) -> AlertSourceCredentialRow | None:
        statement = select(AlertSourceCredentialRow).where(
            AlertSourceCredentialRow.id == credential_id,
            AlertSourceCredentialRow.alert_source_id == source_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def find_credential_by_id(
        self,
        credential_id: str,
        *,
        for_update: bool = False,
    ) -> AlertSourceCredentialRow | None:
        statement = select(AlertSourceCredentialRow).where(
            AlertSourceCredentialRow.id == credential_id
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def list_credentials(self, source_id: str) -> tuple[AlertSourceCredentialRow, ...]:
        return tuple(
            self._session.scalars(
                select(AlertSourceCredentialRow)
                .where(AlertSourceCredentialRow.alert_source_id == source_id)
                .order_by(AlertSourceCredentialRow.created_at, AlertSourceCredentialRow.id)
            )
        )

    def active_credential_count(self, source_id: str) -> int:
        return (
            self._session.scalar(
                select(func.count())
                .select_from(AlertSourceCredentialRow)
                .where(
                    AlertSourceCredentialRow.alert_source_id == source_id,
                    AlertSourceCredentialRow.state == "ACTIVE",
                )
            )
            or 0
        )

    def active_alert_count(self, source_id: str) -> int:
        return (
            self._session.scalar(
                select(func.count())
                .select_from(AlertRow)
                .where(
                    AlertRow.alert_source_id == source_id,
                    AlertRow.state == "ACTIVE",
                )
            )
            or 0
        )

    def add_credential(self, row: AlertSourceCredentialRow) -> None:
        self._session.add(row)
        self._session.flush()

    def find_operation(
        self,
        scope: str,
        idempotency_key_hash: str,
    ) -> AlertSourceOperationRow | None:
        return self._session.scalar(
            select(AlertSourceOperationRow).where(
                AlertSourceOperationRow.scope == scope,
                AlertSourceOperationRow.idempotency_key_hash == idempotency_key_hash,
            )
        )

    def add_operation(self, row: AlertSourceOperationRow) -> None:
        self._session.add(row)
        self._session.flush()

    def list_receipts(
        self,
        source_id: str,
        *,
        since: datetime,
        limit: int,
        offset: int,
    ) -> tuple[AlertSourceReceiptRow, ...]:
        return tuple(
            self._session.scalars(
                select(AlertSourceReceiptRow)
                .where(
                    AlertSourceReceiptRow.alert_source_id == source_id,
                    AlertSourceReceiptRow.received_at >= since,
                )
                .order_by(
                    AlertSourceReceiptRow.received_at.desc(),
                    AlertSourceReceiptRow.id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
        )

    def count_receipts(self, source_id: str, *, since: datetime) -> int:
        return (
            self._session.scalar(
                select(func.count())
                .select_from(AlertSourceReceiptRow)
                .where(
                    AlertSourceReceiptRow.alert_source_id == source_id,
                    AlertSourceReceiptRow.received_at >= since,
                )
            )
            or 0
        )

    def add_receipt(self, row: AlertSourceReceiptRow) -> None:
        self._session.add(row)
        self._session.flush()

    def prune_receipts(
        self,
        source_id: str,
        *,
        cutoff: datetime,
        keep: int,
    ) -> None:
        self._session.execute(
            delete(AlertSourceReceiptRow).where(
                AlertSourceReceiptRow.alert_source_id == source_id,
                AlertSourceReceiptRow.received_at < cutoff,
            )
        )
        excess_ids = tuple(
            self._session.scalars(
                select(AlertSourceReceiptRow.id)
                .where(AlertSourceReceiptRow.alert_source_id == source_id)
                .order_by(
                    AlertSourceReceiptRow.received_at.desc(),
                    AlertSourceReceiptRow.id.desc(),
                )
                .offset(keep)
            )
        )
        if excess_ids:
            self._session.execute(
                delete(AlertSourceReceiptRow).where(AlertSourceReceiptRow.id.in_(excess_ids))
            )
        self._session.flush()

    def flush(self) -> None:
        self._session.flush()
