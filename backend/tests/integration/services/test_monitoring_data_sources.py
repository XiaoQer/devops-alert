from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import Engine

from incident_intelligence.persistence.session import make_session_factory
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.monitoring_data_sources import (
    ConnectionTestResult,
    MonitoringCredentialResolver,
    MonitoringDataSource,
    MonitoringDataSourceConflict,
    MonitoringDataSourceService,
)

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def test_service_enforces_one_enabled_source_per_environment_and_type(
    migrated_engine: Engine,
) -> None:
    service = _service(migrated_engine)
    first = service.create(
        name="测试 Prometheus",
        environment="testing",
        source_type="PROMETHEUS",
        base_url="http://prometheus:9090",
        credential_env_key="II_PROMETHEUS_TOKEN",
        field_mapping={"service": "service", "environment": "environment"},
        verify_tls=True,
        enabled=True,
    )

    assert first.credential_configured is False
    assert service.list().items == (first,)
    with pytest.raises(MonitoringDataSourceConflict):
        service.create(
            name="备用 Prometheus",
            environment="testing",
            source_type="PROMETHEUS",
            base_url="http://prometheus-backup:9090",
            credential_env_key=None,
            field_mapping={"service": "service", "environment": "environment"},
            verify_tls=True,
            enabled=True,
        )


def test_credentials_are_resolved_only_from_environment() -> None:
    source = MonitoringDataSource(
        id="mds_11111111111111111111111111111111",
        name="测试 Prometheus",
        environment="testing",
        source_type="PROMETHEUS",
        base_url="http://prometheus:9090",
        credential_env_key="II_PROMETHEUS_TOKEN",
        field_mapping={"service": "service", "environment": "environment"},
        verify_tls=True,
        enabled=True,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    resolver = MonitoringCredentialResolver({"II_PROMETHEUS_TOKEN": "secret-value"})

    assert resolver.resolve(source) == "secret-value"
    assert resolver.capability(source).configured is True
    assert "secret-value" not in repr(resolver.capability(source))


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://prometheus:21",
        "http://user:password@prometheus:9090",
        "http://prometheus:9090?token=secret",
        "http://prometheus:9090/#fragment",
    ],
)
def test_source_rejects_unsafe_base_urls(base_url: str) -> None:
    with pytest.raises(ValidationError):
        MonitoringDataSource(
            id="mds_11111111111111111111111111111111",
            name="测试 Prometheus",
            environment="testing",
            source_type="PROMETHEUS",
            base_url=base_url,
            credential_env_key=None,
            field_mapping={"service": "service"},
            verify_tls=True,
            enabled=True,
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )


def test_connection_result_is_persisted_without_changing_configuration_version(
    migrated_engine: Engine,
) -> None:
    service = _service(
        migrated_engine,
        connection_tester=_AvailableConnectionTester(),
    )
    source = service.create(
        name="测试 Prometheus",
        environment="testing",
        source_type="PROMETHEUS",
        base_url="http://prometheus:9090",
        credential_env_key=None,
        field_mapping={},
        verify_tls=True,
        enabled=True,
    )

    result = service.test_connection(source.id)
    persisted = service.list().items[0]

    assert result.state == "AVAILABLE"
    assert persisted.version == 1
    assert persisted.last_test_state == "AVAILABLE"
    assert persisted.last_test_latency_ms == 23
    assert persisted.last_compatible_version == "2.54.1"
    assert persisted.last_test_error_code is None
    assert persisted.last_tested_at == NOW


class _AvailableConnectionTester:
    def test(
        self,
        source: MonitoringDataSource,
        *,
        credential: str | None,
    ) -> ConnectionTestResult:
        del source, credential
        return ConnectionTestResult(
            state="AVAILABLE",
            latency_ms=23,
            compatible_version="2.54.1",
        )


def _service(
    engine: Engine,
    *,
    connection_tester=None,
) -> MonitoringDataSourceService:
    factory = make_session_factory(engine)
    return MonitoringDataSourceService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
        credential_resolver=MonitoringCredentialResolver({}),
        connection_tester=connection_tester,
        clock=lambda: NOW,
    )
