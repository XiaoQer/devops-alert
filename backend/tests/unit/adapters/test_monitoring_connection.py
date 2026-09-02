from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from incident_intelligence.adapters.monitoring_connection import (
    HttpMonitoringConnectionTester,
)
from incident_intelligence.adapters.monitoring_http import (
    MonitoringHttpResponse,
    MonitoringRetryableError,
)
from incident_intelligence.services.monitoring_data_sources import MonitoringDataSource

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


class _Transport:
    def __init__(self, response: MonitoringHttpResponse | Exception) -> None:
        self.response = response
        self.requests: list[dict[str, object]] = []

    def request(self, method: str, url: str, **kwargs: object) -> MonitoringHttpResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.mark.parametrize(
    ("source_type", "path", "method", "body", "version"),
    [
        (
            "PROMETHEUS",
            "/api/v1/status/buildinfo",
            "GET",
            {"status": "success", "data": {"version": "2.54.1"}},
            "2.54.1",
        ),
        (
            "ELASTICSEARCH",
            "/",
            "GET",
            {"version": {"number": "8.15.0"}},
            "8.15.0",
        ),
        (
            "SKYWALKING",
            "/graphql",
            "POST",
            {"data": {"version": "10.1.0"}},
            "10.1.0",
        ),
    ],
)
def test_connection_tester_uses_product_read_only_probe_and_validates_protocol(
    source_type: str,
    path: str,
    method: str,
    body: dict[str, object],
    version: str,
) -> None:
    transport = _Transport(
        MonitoringHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=json.dumps(body).encode(),
        )
    )
    tester = HttpMonitoringConnectionTester(
        transport=transport,
        timer=_sequence_timer(5.0, 5.037),
    )

    result = tester.test(_source(source_type), credential="monitoring-token")

    assert result.model_dump() == {
        "state": "AVAILABLE",
        "latency_ms": 37,
        "compatible_version": version,
        "error_code": None,
    }
    assert transport.requests == [
        {
            "method": method,
            "url": f"http://monitoring.local{path}",
            "headers": {
                "Accept": "application/json",
                "Authorization": "Bearer monitoring-token",
            },
            "form": None,
            "json": None if source_type != "SKYWALKING" else {"query": "query { version }"},
            "timeout_seconds": 5,
            "max_response_bytes": 65_536,
        }
    ]


def test_connection_tester_rejects_successful_http_with_wrong_product_payload() -> None:
    transport = _Transport(
        MonitoringHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=b'{"status":"ok"}',
        )
    )
    tester = HttpMonitoringConnectionTester(
        transport=transport,
        timer=_sequence_timer(1.0, 1.004),
    )

    result = tester.test(_source("PROMETHEUS"), credential=None)

    assert result.state == "UNAVAILABLE"
    assert result.latency_ms == 4
    assert result.error_code == "monitoring_protocol_incompatible"
    assert result.compatible_version is None


def test_connection_tester_returns_stable_code_without_upstream_details() -> None:
    transport = _Transport(MonitoringRetryableError("network_timeout"))
    tester = HttpMonitoringConnectionTester(
        transport=transport,
        timer=_sequence_timer(1.0, 6.0),
    )

    result = tester.test(_source("ELASTICSEARCH"), credential=None)

    assert result.model_dump() == {
        "state": "UNAVAILABLE",
        "latency_ms": 5_000,
        "compatible_version": None,
        "error_code": "network_timeout",
    }


def test_connection_tester_does_not_call_upstream_when_configured_credential_is_missing() -> None:
    transport = _Transport(RuntimeError("不应调用"))
    tester = HttpMonitoringConnectionTester(transport=transport)
    source = _source("PROMETHEUS").model_copy(update={"credential_env_key": "II_PROMETHEUS_TOKEN"})

    result = tester.test(source, credential=None)

    assert result.state == "UNAVAILABLE"
    assert result.error_code == "monitoring_credential_missing"
    assert transport.requests == []


def _source(source_type: str) -> MonitoringDataSource:
    field_mapping = {"graphql_path": "/graphql"} if source_type == "SKYWALKING" else {}
    return MonitoringDataSource(
        id="mds_11111111111111111111111111111111",
        name="测试监控源",
        environment="testing",
        source_type=source_type,
        base_url="http://monitoring.local",
        credential_env_key=None,
        field_mapping=field_mapping,
        verify_tls=True,
        enabled=True,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _sequence_timer(*values: float):
    remaining = iter(values)
    return lambda: next(remaining)
