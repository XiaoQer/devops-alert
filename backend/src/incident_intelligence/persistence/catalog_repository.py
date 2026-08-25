from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from incident_intelligence.persistence.models import (
    ServiceCatalogEntryRow,
    ServiceCatalogStateRow,
    ServiceDependencyRow,
)


class ServiceCatalogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_service(
        self,
        service_id: str,
        *,
        for_update: bool = False,
    ) -> ServiceCatalogEntryRow | None:
        statement = select(ServiceCatalogEntryRow).where(ServiceCatalogEntryRow.id == service_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def find_service_identity(
        self,
        service: str,
        environment: str,
    ) -> ServiceCatalogEntryRow | None:
        return self._session.scalar(
            select(ServiceCatalogEntryRow).where(
                ServiceCatalogEntryRow.service == service,
                ServiceCatalogEntryRow.environment == environment,
            )
        )

    def add_service(self, row: ServiceCatalogEntryRow) -> None:
        self._session.add(row)
        self._session.flush()

    def list_services(
        self,
        *,
        state: str | None,
        environment: str | None,
        limit: int,
        offset: int,
    ) -> tuple[ServiceCatalogEntryRow, ...]:
        statement = select(ServiceCatalogEntryRow)
        if state is not None:
            statement = statement.where(ServiceCatalogEntryRow.state == state)
        if environment is not None:
            statement = statement.where(ServiceCatalogEntryRow.environment == environment)
        statement = (
            statement.order_by(
                ServiceCatalogEntryRow.service,
                ServiceCatalogEntryRow.environment,
                ServiceCatalogEntryRow.id,
            )
            .limit(limit)
            .offset(offset)
        )
        return tuple(self._session.scalars(statement))

    def lock_graph_state(self) -> ServiceCatalogStateRow:
        row = self._session.scalar(
            select(ServiceCatalogStateRow)
            .where(ServiceCatalogStateRow.id == "global")
            .with_for_update()
        )
        if row is None:
            raise RuntimeError("服务目录图状态不存在")
        return row

    def find_dependency(
        self,
        dependency_id: str,
        *,
        for_update: bool = False,
    ) -> ServiceDependencyRow | None:
        statement = select(ServiceDependencyRow).where(ServiceDependencyRow.id == dependency_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def find_dependency_edge(
        self,
        caller_service_id: str,
        dependency_service_id: str,
    ) -> ServiceDependencyRow | None:
        return self._session.scalar(
            select(ServiceDependencyRow).where(
                ServiceDependencyRow.caller_service_id == caller_service_id,
                ServiceDependencyRow.dependency_service_id == dependency_service_id,
            )
        )

    def active_edges(self) -> tuple[ServiceDependencyRow, ...]:
        return tuple(
            self._session.scalars(
                select(ServiceDependencyRow).where(ServiceDependencyRow.state == "ACTIVE")
            )
        )

    def add_dependency(self, row: ServiceDependencyRow) -> None:
        self._session.add(row)
        self._session.flush()

    def list_dependencies(
        self,
        *,
        service_id: str | None,
        state: str | None,
        limit: int,
        offset: int,
    ) -> tuple[ServiceDependencyRow, ...]:
        statement = select(ServiceDependencyRow)
        if service_id is not None:
            statement = statement.where(
                (ServiceDependencyRow.caller_service_id == service_id)
                | (ServiceDependencyRow.dependency_service_id == service_id)
            )
        if state is not None:
            statement = statement.where(ServiceDependencyRow.state == state)
        statement = (
            statement.order_by(
                ServiceDependencyRow.caller_service_id,
                ServiceDependencyRow.dependency_service_id,
                ServiceDependencyRow.id,
            )
            .limit(limit)
            .offset(offset)
        )
        return tuple(self._session.scalars(statement))

    def flush(self) -> None:
        self._session.flush()
