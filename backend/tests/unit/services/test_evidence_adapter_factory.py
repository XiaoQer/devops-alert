from __future__ import annotations

from datetime import UTC, datetime

import pytest

from incident_intelligence.adapters.monitoring_http import MonitoringPermanentError
from incident_intelligence.adapters.prometheus import PrometheusEvidenceAdapter
from incident_intelligence.persistence.evidence_repository import MonitoringDataSourceRecord
from incident_intelligence.services.evidence_adapter_factory import MonitoringEvidenceAdapterFactory
from incident_intelligence.services.monitoring_data_sources import MonitoringCredentialResolver

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def test_factory_builds_prometheus_adapter_without_a_credential() -> None:
    factory = MonitoringEvidenceAdapterFactory(credential_resolver=MonitoringCredentialResolver({}))

    adapter = factory.create(_source(credential_env_key=None))

    assert isinstance(adapter, PrometheusEvidenceAdapter)


def test_factory_rejects_a_configured_but_missing_credential() -> None:
    factory = MonitoringEvidenceAdapterFactory(credential_resolver=MonitoringCredentialResolver({}))

    with pytest.raises(MonitoringPermanentError, match="monitoring_credential_missing"):
        factory.create(_source(credential_env_key="II_PROM_TOKEN"))


def _source(*, credential_env_key: str | None) -> MonitoringDataSourceRecord:
    return MonitoringDataSourceRecord(
        id="mds_11111111111111111111111111111111",
        name="Prometheus",
        environment="testing",
        source_type="PROMETHEUS",
        base_url="http://prometheus:9090",
        credential_env_key=credential_env_key,
        field_mapping={},
        verify_tls=True,
        enabled=True,
        version=1,
        last_test_state=None,
        last_test_latency_ms=None,
        last_compatible_version=None,
        last_test_error_code=None,
        last_tested_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
