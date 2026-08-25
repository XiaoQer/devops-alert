from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.enums import CatalogState
from incident_intelligence.persistence.models import (
    AuditEventRow,
    ServiceCatalogEntryRow,
    ServiceCatalogStateRow,
    ServiceDependencyRow,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.catalog import (
    CatalogConflict,
    CatalogResourceNotFound,
    CatalogVersionConflict,
    CreateDependencyCommand,
    CreateServiceCommand,
    InvalidDependency,
    ServiceCatalogService,
    UpdateDependencyCommand,
    UpdateServiceCommand,
)

NOW = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)


@pytest.fixture
def session_factory(migrated_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def service(session_factory: sessionmaker[Session]) -> ServiceCatalogService:
    return ServiceCatalogService(
        uow_factory=partial(SqlAlchemyUnitOfWork, session_factory),
        clock=lambda: NOW,
    )


def create_service(
    service: ServiceCatalogService,
    name: str,
    environment: str = "production",
) -> object:
    return service.create_service(
        CreateServiceCommand(
            service=name,
            environment=environment,
            owner_team="platform",
        ),
        actor="manual-api-client",
        request_id=f"req-{name}-{environment}",
    )


def test_service_identity_is_unique_and_update_requires_expected_version(
    service: ServiceCatalogService,
    session_factory: sessionmaker[Session],
) -> None:
    created = service.create_service(
        CreateServiceCommand(
            service="payment-api",
            environment="production",
            owner_team="payments",
        ),
        actor="manual-api-client",
        request_id="req-1",
    )

    with pytest.raises(CatalogConflict) as duplicate:
        service.create_service(
            CreateServiceCommand(
                service="payment-api",
                environment="production",
                owner_team="payments",
            ),
            actor="manual-api-client",
            request_id="req-2",
        )
    assert duplicate.value.reason_code == "catalog_conflict"

    with pytest.raises(CatalogVersionConflict) as stale:
        service.update_service(
            created.id,
            UpdateServiceCommand(expected_version=99, owner_team="platform"),
            actor="manual-api-client",
            request_id="req-3",
        )
    assert stale.value.reason_code == "catalog_version_conflict"

    updated = service.update_service(
        created.id,
        UpdateServiceCommand(expected_version=1, owner_team="platform"),
        actor="manual-api-client",
        request_id="req-4",
    )
    inactive = service.update_service(
        created.id,
        UpdateServiceCommand(expected_version=2, state="INACTIVE"),
        actor="manual-api-client",
        request_id="req-5",
    )

    assert updated.owner_team == "platform"
    assert updated.version == 2
    assert inactive.state == "INACTIVE"
    assert inactive.version == 3
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ServiceCatalogEntryRow)) == 1
        audits = list(session.scalars(select(AuditEventRow).order_by(AuditEventRow.created_at)))
    assert Counter(audit.action for audit in audits) == {
        "catalog.service_created": 1,
        "catalog.service_updated": 2,
    }
    assert all(
        set(audit.details)
        <= {
            "reason_code",
            "previous_state",
            "new_state",
            "previous_version",
            "new_version",
        }
        for audit in audits
    )


def test_unknown_service_update_is_safe_not_found(service: ServiceCatalogService) -> None:
    with pytest.raises(CatalogResourceNotFound) as error:
        service.update_service(
            "svc_" + "f" * 32,
            UpdateServiceCommand(expected_version=1, state="INACTIVE"),
            actor="manual-api-client",
            request_id="req-missing",
        )
    assert error.value.reason_code == "catalog_resource_not_found"


def test_dependency_cycle_is_rejected_atomically_and_graph_version_advances(
    service: ServiceCatalogService,
    session_factory: sessionmaker[Session],
) -> None:
    service_a = create_service(service, "service-a")
    service_b = create_service(service, "service-b")
    service_c = create_service(service, "service-c")
    dependency_ab = service.create_dependency(
        CreateDependencyCommand(
            caller_service_id=service_a.id,
            dependency_service_id=service_b.id,
        ),
        actor="manual-api-client",
        request_id="req-ab",
    )
    service.create_dependency(
        CreateDependencyCommand(
            caller_service_id=service_b.id,
            dependency_service_id=service_c.id,
        ),
        actor="manual-api-client",
        request_id="req-bc",
    )

    with pytest.raises(InvalidDependency) as cycle:
        service.create_dependency(
            CreateDependencyCommand(
                caller_service_id=service_c.id,
                dependency_service_id=service_a.id,
            ),
            actor="manual-api-client",
            request_id="req-ca",
        )
    assert cycle.value.reason_code == "dependency_cycle"

    inactive = service.update_dependency(
        dependency_ab.id,
        UpdateDependencyCommand(expected_version=1, state="INACTIVE"),
        actor="manual-api-client",
        request_id="req-disable-ab",
    )
    assert inactive.state == "INACTIVE"
    assert inactive.version == 2

    with session_factory() as session:
        pairs = set(
            session.execute(
                select(
                    ServiceDependencyRow.caller_service_id,
                    ServiceDependencyRow.dependency_service_id,
                    ServiceDependencyRow.state,
                )
            )
        )
        graph = session.get(ServiceCatalogStateRow, "global")
    assert pairs == {
        (service_a.id, service_b.id, "INACTIVE"),
        (service_b.id, service_c.id, "ACTIVE"),
    }
    assert graph is not None
    assert graph.graph_version == 4


@pytest.mark.parametrize(
    ("kind", "expected_reason"),
    [
        ("self", "dependency_self_reference"),
        ("cross_environment", "dependency_cross_environment"),
        ("duplicate", "catalog_conflict"),
    ],
)
def test_invalid_dependency_writes_leave_no_extra_active_edge(
    service: ServiceCatalogService,
    session_factory: sessionmaker[Session],
    kind: str,
    expected_reason: str,
) -> None:
    production = create_service(service, "payment-api")
    other = (
        create_service(service, "inventory-api", "staging")
        if kind == "cross_environment"
        else create_service(service, "inventory-api")
    )
    command = CreateDependencyCommand(
        caller_service_id=production.id,
        dependency_service_id=production.id if kind == "self" else other.id,
    )
    if kind == "duplicate":
        service.create_dependency(
            command,
            actor="manual-api-client",
            request_id="req-first",
        )

    expected_error = CatalogConflict if kind == "duplicate" else InvalidDependency
    with pytest.raises(expected_error) as error:
        service.create_dependency(
            command,
            actor="manual-api-client",
            request_id="req-invalid",
        )
    assert error.value.reason_code == expected_reason

    with session_factory() as session:
        edge_count = session.scalar(select(func.count()).select_from(ServiceDependencyRow))
    assert edge_count == (1 if kind == "duplicate" else 0)


def test_reactivating_dependency_cannot_complete_a_cycle(
    service: ServiceCatalogService,
    session_factory: sessionmaker[Session],
) -> None:
    service_a = create_service(service, "service-a")
    service_b = create_service(service, "service-b")
    service_c = create_service(service, "service-c")
    dependency_ab = service.create_dependency(
        CreateDependencyCommand(
            caller_service_id=service_a.id,
            dependency_service_id=service_b.id,
        ),
        actor="manual-api-client",
        request_id="req-ab",
    )
    service.create_dependency(
        CreateDependencyCommand(
            caller_service_id=service_b.id,
            dependency_service_id=service_c.id,
        ),
        actor="manual-api-client",
        request_id="req-bc",
    )
    service.update_dependency(
        dependency_ab.id,
        UpdateDependencyCommand(expected_version=1, state="INACTIVE"),
        actor="manual-api-client",
        request_id="req-disable-ab",
    )
    service.create_dependency(
        CreateDependencyCommand(
            caller_service_id=service_c.id,
            dependency_service_id=service_a.id,
        ),
        actor="manual-api-client",
        request_id="req-ca",
    )

    with pytest.raises(InvalidDependency) as error:
        service.update_dependency(
            dependency_ab.id,
            UpdateDependencyCommand(expected_version=2, state="ACTIVE"),
            actor="manual-api-client",
            request_id="req-enable-ab",
        )
    assert error.value.reason_code == "dependency_cycle"

    with session_factory() as session:
        persisted = session.get(ServiceDependencyRow, dependency_ab.id)
    assert persisted is not None
    assert persisted.state == "INACTIVE"
    assert persisted.version == 2


def test_concurrent_opposite_edges_are_serialized_to_one_success(
    service: ServiceCatalogService,
    session_factory: sessionmaker[Session],
) -> None:
    service_a = create_service(service, "service-a")
    service_b = create_service(service, "service-b")
    ready = Barrier(2)

    def create_edge(caller_id: str, dependency_id: str, request_id: str) -> str:
        ready.wait(timeout=5)
        try:
            service.create_dependency(
                CreateDependencyCommand(
                    caller_service_id=caller_id,
                    dependency_service_id=dependency_id,
                ),
                actor="manual-api-client",
                request_id=request_id,
            )
        except InvalidDependency as error:
            return error.reason_code
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = set(
            executor.map(
                lambda arguments: create_edge(*arguments),
                [
                    (service_a.id, service_b.id, "req-ab"),
                    (service_b.id, service_a.id, "req-ba"),
                ],
            )
        )

    assert results == {"created", "dependency_cycle"}
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ServiceDependencyRow)) == 1
        graph = session.get(ServiceCatalogStateRow, "global")
    assert graph is not None
    assert graph.graph_version == 2


def test_catalog_lists_are_filtered_ordered_and_paginated(
    service: ServiceCatalogService,
) -> None:
    create_service(service, "service-c")
    service_b = create_service(service, "service-b")
    service_a = create_service(service, "service-a")
    service.update_service(
        service_b.id,
        UpdateServiceCommand(expected_version=1, state="INACTIVE"),
        actor="manual-api-client",
        request_id="req-disable-b",
    )
    dependency = service.create_dependency(
        CreateDependencyCommand(
            caller_service_id=service_a.id,
            dependency_service_id=service_b.id,
        ),
        actor="manual-api-client",
        request_id="req-ab",
    )

    assert [item.service for item in service.list_services(limit=2, offset=1)] == [
        "service-b",
        "service-c",
    ]
    assert [
        item.service
        for item in service.list_services(
            state=CatalogState.ACTIVE,
            environment="production",
        )
    ] == ["service-a", "service-c"]
    assert [item.id for item in service.list_dependencies(service_id=service_a.id)] == [
        dependency.id
    ]


@pytest.mark.parametrize(
    ("method", "arguments"),
    [
        ("services", {"limit": 101}),
        ("services", {"limit": 0}),
        ("services", {"offset": 10_001}),
        ("dependencies", {"limit": 101}),
        ("dependencies", {"offset": -1}),
    ],
)
def test_catalog_service_rejects_unbounded_pagination(
    service: ServiceCatalogService,
    method: str,
    arguments: dict[str, int],
) -> None:
    with pytest.raises(ValueError, match="catalog_pagination_out_of_range"):
        if method == "services":
            service.list_services(**arguments)
        else:
            service.list_dependencies(**arguments)
