from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy.exc import IntegrityError

from incident_intelligence.domain.catalog import (
    OwnerTeam,
    ServiceCatalogEntry,
    ServiceDependency,
)
from incident_intelligence.domain.enums import CatalogState, DependencyState
from incident_intelligence.domain.forbidden_identity import reject_forbidden_identity
from incident_intelligence.domain.models import Environment, ServiceName
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.catalog_repository import ServiceCatalogRepository
from incident_intelligence.persistence.models import (
    ServiceCatalogEntryRow,
    ServiceDependencyRow,
)
from incident_intelligence.persistence.repositories import RecordRepositories
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork

CatalogId = Annotated[str, StringConstraints(pattern=r"^svc_[0-9a-f]{32}$")]
DependencyId = Annotated[str, StringConstraints(pattern=r"^dep_[0-9a-f]{32}$")]


class CreateServiceCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    service: ServiceName
    environment: Environment
    owner_team: OwnerTeam


class UpdateServiceCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_version: int = Field(ge=1)
    owner_team: OwnerTeam | None = None
    state: CatalogState | None = None

    @model_validator(mode="after")
    def require_change(self) -> UpdateServiceCommand:
        if self.owner_team is None and self.state is None:
            raise ValueError("至少提供一个服务目录变更字段")
        return self


class CreateDependencyCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    caller_service_id: CatalogId
    dependency_service_id: CatalogId


class UpdateDependencyCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_version: int = Field(ge=1)
    state: DependencyState


@dataclass(frozen=True, slots=True)
class CatalogConflict(Exception):
    reason_code: str = "catalog_conflict"


@dataclass(frozen=True, slots=True)
class CatalogVersionConflict(Exception):
    reason_code: str = "catalog_version_conflict"


@dataclass(frozen=True, slots=True)
class InvalidDependency(Exception):
    reason_code: str = "invalid_dependency"


@dataclass(frozen=True, slots=True)
class CatalogResourceNotFound(Exception):
    reason_code: str = "catalog_resource_not_found"


class ServiceCatalogService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[IdPrefix], str] = new_id,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory

    def create_service(
        self,
        command: CreateServiceCommand,
        *,
        actor: str,
        request_id: str,
    ) -> ServiceCatalogEntry:
        reject_forbidden_identity(command.model_dump(mode="json"))
        try:
            with self._uow_factory() as uow:
                catalog = _catalog(uow)
                if catalog.find_service_identity(command.service, command.environment):
                    raise CatalogConflict()
                now = self._now()
                row = ServiceCatalogEntryRow(
                    id=self._id_factory("svc"),
                    service=command.service,
                    environment=command.environment,
                    owner_team=command.owner_team,
                    state=CatalogState.ACTIVE.value,
                    created_at=now,
                    updated_at=now,
                    version=1,
                )
                catalog.add_service(row)
                _records(uow).add_audit(
                    audit_id=self._id_factory("aud"),
                    actor=actor,
                    action="catalog.service_created",
                    resource_type="service_catalog",
                    resource_id=row.id,
                    request_id=request_id,
                    details={
                        "reason_code": "catalog_service_created",
                        "new_state": row.state,
                        "new_version": "1",
                    },
                    created_at=now,
                )
                uow.commit()
                return _service_from_row(row)
        except IntegrityError as error:
            raise CatalogConflict() from error

    def update_service(
        self,
        service_id: CatalogId,
        command: UpdateServiceCommand,
        *,
        actor: str,
        request_id: str,
    ) -> ServiceCatalogEntry:
        reject_forbidden_identity(command.model_dump(mode="json"))
        with self._uow_factory() as uow:
            row = _catalog(uow).find_service(service_id, for_update=True)
            if row is None:
                raise CatalogResourceNotFound()
            if row.version != command.expected_version:
                raise CatalogVersionConflict()
            previous_state = row.state
            previous_version = row.version
            if command.owner_team is not None:
                row.owner_team = command.owner_team
            if command.state is not None:
                row.state = command.state.value
            row.version += 1
            row.updated_at = self._now()
            _catalog(uow).flush()
            self._audit_update(
                uow,
                action="catalog.service_updated",
                resource_type="service_catalog",
                resource_id=row.id,
                actor=actor,
                request_id=request_id,
                previous_state=previous_state,
                new_state=row.state,
                previous_version=previous_version,
                new_version=row.version,
                created_at=row.updated_at,
            )
            uow.commit()
            return _service_from_row(row)

    def create_dependency(
        self,
        command: CreateDependencyCommand,
        *,
        actor: str,
        request_id: str,
    ) -> ServiceDependency:
        reject_forbidden_identity(command.model_dump(mode="json"))
        try:
            with self._uow_factory() as uow:
                catalog = _catalog(uow)
                graph = catalog.lock_graph_state()
                caller, dependency = self._validated_endpoints(catalog, command)
                if catalog.find_dependency_edge(caller.id, dependency.id) is not None:
                    raise CatalogConflict()
                edges = _active_pairs(catalog)
                edges.add((caller.id, dependency.id))
                if _has_cycle(edges):
                    raise InvalidDependency("dependency_cycle")
                now = self._now()
                row = ServiceDependencyRow(
                    id=self._id_factory("dep"),
                    caller_service_id=caller.id,
                    dependency_service_id=dependency.id,
                    state=DependencyState.ACTIVE.value,
                    created_at=now,
                    updated_at=now,
                    version=1,
                )
                catalog.add_dependency(row)
                graph.graph_version += 1
                graph.updated_at = now
                catalog.flush()
                _records(uow).add_audit(
                    audit_id=self._id_factory("aud"),
                    actor=actor,
                    action="catalog.dependency_created",
                    resource_type="service_dependency",
                    resource_id=row.id,
                    request_id=request_id,
                    details={
                        "reason_code": "catalog_dependency_created",
                        "new_state": row.state,
                        "new_version": "1",
                    },
                    created_at=now,
                )
                uow.commit()
                return _dependency_from_row(row)
        except IntegrityError as error:
            raise CatalogConflict() from error

    def update_dependency(
        self,
        dependency_id: DependencyId,
        command: UpdateDependencyCommand,
        *,
        actor: str,
        request_id: str,
    ) -> ServiceDependency:
        reject_forbidden_identity(command.model_dump(mode="json"))
        with self._uow_factory() as uow:
            catalog = _catalog(uow)
            graph = catalog.lock_graph_state()
            row = catalog.find_dependency(dependency_id, for_update=True)
            if row is None:
                raise CatalogResourceNotFound()
            if row.version != command.expected_version:
                raise CatalogVersionConflict()
            if command.state is DependencyState.ACTIVE:
                caller = catalog.find_service(row.caller_service_id, for_update=True)
                dependency = catalog.find_service(
                    row.dependency_service_id,
                    for_update=True,
                )
                if caller is None or dependency is None:
                    raise CatalogResourceNotFound()
                self._validate_dependency(caller, dependency)
                edges = {
                    pair
                    for pair in _active_pairs(catalog)
                    if pair != (row.caller_service_id, row.dependency_service_id)
                }
                edges.add((row.caller_service_id, row.dependency_service_id))
                if _has_cycle(edges):
                    raise InvalidDependency("dependency_cycle")
            previous_state = row.state
            previous_version = row.version
            now = self._now()
            row.state = command.state.value
            row.version += 1
            row.updated_at = now
            graph.graph_version += 1
            graph.updated_at = now
            catalog.flush()
            self._audit_update(
                uow,
                action="catalog.dependency_updated",
                resource_type="service_dependency",
                resource_id=row.id,
                actor=actor,
                request_id=request_id,
                previous_state=previous_state,
                new_state=row.state,
                previous_version=previous_version,
                new_version=row.version,
                created_at=now,
            )
            uow.commit()
            return _dependency_from_row(row)

    def list_services(
        self,
        *,
        state: CatalogState | None = None,
        environment: Environment | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[ServiceCatalogEntry, ...]:
        _validate_pagination(limit, offset)
        with self._uow_factory() as uow:
            rows = _catalog(uow).list_services(
                state=state.value if state else None,
                environment=environment,
                limit=limit,
                offset=offset,
            )
            return tuple(_service_from_row(row) for row in rows)

    def list_dependencies(
        self,
        *,
        service_id: str | None = None,
        state: DependencyState | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[ServiceDependency, ...]:
        _validate_pagination(limit, offset)
        with self._uow_factory() as uow:
            rows = _catalog(uow).list_dependencies(
                service_id=service_id,
                state=state.value if state else None,
                limit=limit,
                offset=offset,
            )
            return tuple(_dependency_from_row(row) for row in rows)

    def _validated_endpoints(
        self,
        catalog: ServiceCatalogRepository,
        command: CreateDependencyCommand,
    ) -> tuple[ServiceCatalogEntryRow, ServiceCatalogEntryRow]:
        caller = catalog.find_service(command.caller_service_id, for_update=True)
        dependency = catalog.find_service(command.dependency_service_id, for_update=True)
        if caller is None or dependency is None:
            raise CatalogResourceNotFound()
        self._validate_dependency(caller, dependency)
        return caller, dependency

    @staticmethod
    def _validate_dependency(
        caller: ServiceCatalogEntryRow,
        dependency: ServiceCatalogEntryRow,
    ) -> None:
        if caller.id == dependency.id:
            raise InvalidDependency("dependency_self_reference")
        if caller.environment != dependency.environment:
            raise InvalidDependency("dependency_cross_environment")

    def _now(self) -> datetime:
        return self._clock().astimezone(UTC)

    def _audit_update(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        action: str,
        resource_type: str,
        resource_id: str,
        actor: str,
        request_id: str,
        previous_state: str,
        new_state: str,
        previous_version: int,
        new_version: int,
        created_at: datetime,
    ) -> None:
        _records(uow).add_audit(
            audit_id=self._id_factory("aud"),
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            details={
                "reason_code": action.replace(".", "_"),
                "previous_state": previous_state,
                "new_state": new_state,
                "previous_version": str(previous_version),
                "new_version": str(new_version),
            },
            created_at=created_at,
        )


def _catalog(uow: SqlAlchemyUnitOfWork) -> ServiceCatalogRepository:
    if uow.catalog is None:
        raise RuntimeError("工作单元没有可用服务目录仓储")
    return uow.catalog


def _records(uow: SqlAlchemyUnitOfWork) -> RecordRepositories:
    if uow.records is None:
        raise RuntimeError("工作单元没有可用记录仓储")
    return uow.records


def _active_pairs(catalog: ServiceCatalogRepository) -> set[tuple[str, str]]:
    return {(row.caller_service_id, row.dependency_service_id) for row in catalog.active_edges()}


def _has_cycle(edges: set[tuple[str, str]]) -> bool:
    adjacency: dict[str, set[str]] = {}
    for caller, dependency in edges:
        adjacency.setdefault(caller, set()).add(dependency)
        adjacency.setdefault(dependency, set())
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(neighbor) for neighbor in adjacency[node]):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in adjacency)


def _service_from_row(row: ServiceCatalogEntryRow) -> ServiceCatalogEntry:
    return ServiceCatalogEntry.model_validate(
        {
            "id": row.id,
            "service": row.service,
            "environment": row.environment,
            "owner_team": row.owner_team,
            "state": row.state,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "version": row.version,
        }
    )


def _validate_pagination(limit: int, offset: int) -> None:
    if not 1 <= limit <= 100 or not 0 <= offset <= 10_000:
        raise ValueError("catalog_pagination_out_of_range")


def _dependency_from_row(row: ServiceDependencyRow) -> ServiceDependency:
    return ServiceDependency.model_validate(
        {
            "id": row.id,
            "caller_service_id": row.caller_service_id,
            "dependency_service_id": row.dependency_service_id,
            "state": row.state,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "version": row.version,
        }
    )
