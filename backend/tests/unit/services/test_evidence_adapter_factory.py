from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from incident_intelligence.adapters.elasticsearch import ElasticsearchEvidenceAdapter
from incident_intelligence.adapters.monitoring_http import (
    MonitoringHttpResponse,
    MonitoringPermanentError,
)
from incident_intelligence.adapters.prometheus import PrometheusEvidenceAdapter
from incident_intelligence.adapters.skywalking import SkyWalkingEvidenceAdapter
from incident_intelligence.domain.evidence import build_evidence_window
from incident_intelligence.persistence.evidence_repository import MonitoringDataSourceRecord
from incident_intelligence.services.evidence_adapter_factory import MonitoringEvidenceAdapterFactory
from incident_intelligence.services.evidence_planning import EvidenceQueryRequest
from incident_intelligence.services.monitoring_data_sources import MonitoringCredentialResolver

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def test_factory_builds_prometheus_adapter_without_a_credential() -> None:
    factory = MonitoringEvidenceAdapterFactory(credential_resolver=MonitoringCredentialResolver({}))

    adapter = factory.create(_source(credential_env_key=None))

    assert isinstance(adapter, PrometheusEvidenceAdapter)


def test_factory_passes_prometheus_label_mapping_to_query_execution() -> None:
    transport = _CapturingTransport()
    factory = MonitoringEvidenceAdapterFactory(
        credential_resolver=MonitoringCredentialResolver({}),
        transport=transport,
    )
    adapter = factory.create(
        _source(
            credential_env_key=None,
            field_mapping={"environment": "cluster", "service": "service_name"},
        )
    )

    adapter.collect(
        EvidenceQueryRequest(
            execution_key="a" * 64,
            package_id="common-service",
            package_version=1,
            template_id="prom.service.availability",
            template_version=1,
            display_name="服务可用性",
            source_type="PROMETHEUS",
            evidence_type="METRIC_COMPARISON",
            query_name="prom.service.availability",
            controlled_query="avg(up{${environment_matcher}${service_matcher}})",
            parameters={"environment": "testing", "service": "checkout"},
            window=build_evidence_window(NOW, NOW),
        )
    )

    assert transport.query == 'avg(up{cluster="testing",service_name="checkout"})'


def test_factory_rejects_a_configured_but_missing_credential() -> None:
    factory = MonitoringEvidenceAdapterFactory(credential_resolver=MonitoringCredentialResolver({}))

    with pytest.raises(MonitoringPermanentError, match="monitoring_credential_missing"):
        factory.create(_source(credential_env_key="II_PROM_TOKEN"))


@pytest.mark.parametrize(
    ("source_type", "field_mapping", "adapter_type"),
    (
        (
            "ELASTICSEARCH",
            {
                "index": "logs-*",
                "timestamp": "@timestamp",
                "service": "service.name",
                "environment": "environment",
                "message": "message",
            },
            ElasticsearchEvidenceAdapter,
        ),
        ("SKYWALKING", {"graphql_path": "/graphql"}, SkyWalkingEvidenceAdapter),
    ),
)
def test_factory_builds_each_supported_monitoring_adapter(
    source_type: str,
    field_mapping: dict[str, str],
    adapter_type: type,
) -> None:
    factory = MonitoringEvidenceAdapterFactory(credential_resolver=MonitoringCredentialResolver({}))

    adapter = factory.create(
        _source(
            credential_env_key=None,
            source_type=source_type,
            field_mapping=field_mapping,
        )
    )

    assert isinstance(adapter, adapter_type)


def _source(
    *,
    credential_env_key: str | None,
    source_type: str = "PROMETHEUS",
    field_mapping: dict[str, str] | None = None,
) -> MonitoringDataSourceRecord:
    return MonitoringDataSourceRecord(
        id="mds_11111111111111111111111111111111",
        name="Prometheus",
        environment="testing",
        source_type=source_type,
        base_url="http://prometheus:9090",
        credential_env_key=credential_env_key,
        field_mapping=field_mapping or {},
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


class _CapturingTransport:
    query: str | None = None

    def request(self, method: str, url: str, **kwargs: object) -> MonitoringHttpResponse:
        del method, url
        form = kwargs["form"]
        assert isinstance(form, dict)
        self.query = str(form["query"])
        return MonitoringHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=json.dumps(
                {"status": "success", "data": {"resultType": "matrix", "result": []}}
            ).encode(),
        )
