from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from incident_intelligence.adapters.monitoring_http import (
    MonitoringHttpResponse,
    MonitoringPermanentError,
)
from incident_intelligence.adapters.prometheus import PrometheusEvidenceAdapter
from incident_intelligence.domain.evidence import build_evidence_window
from incident_intelligence.services.evidence_planning import EvidenceQueryRequest

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


class _FakeTransport:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.requests: list[dict[str, object]] = []

    def request(self, method: str, url: str, **kwargs: object) -> MonitoringHttpResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        return MonitoringHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=json.dumps(self.payload).encode(),
        )


def test_prometheus_posts_bounded_range_query_and_normalizes_points() -> None:
    transport = _FakeTransport(_matrix_response(series=1))
    adapter = PrometheusEvidenceAdapter(
        base_url="http://prometheus:9090",
        transport=transport,
    )

    result = adapter.collect(_request(service='checkout"api'))

    sent = transport.requests[0]
    assert sent["method"] == "POST"
    assert sent["url"] == "http://prometheus:9090/api/v1/query_range"
    form = sent["form"]
    assert isinstance(form, dict)
    assert form["limit"] == "20"
    assert 'service="checkout\\"api"' in form["query"]
    assert result.state == "SUCCEEDED"
    assert len(result.normalized_result["series"]) == 1


def test_prometheus_rejects_more_than_twenty_series() -> None:
    adapter = PrometheusEvidenceAdapter(
        base_url="http://prometheus:9090",
        transport=_FakeTransport(_matrix_response(series=21)),
    )

    with pytest.raises(MonitoringPermanentError, match="series_limit_exceeded"):
        adapter.collect(_request())


def test_prometheus_treats_nan_as_missing_instead_of_statistic() -> None:
    response = _matrix_response(series=1)
    response["data"]["result"][0]["values"] = [
        [NOW.timestamp(), "NaN"],
        [NOW.timestamp() + 15, "2.5"],
    ]
    adapter = PrometheusEvidenceAdapter(
        base_url="http://prometheus:9090",
        transport=_FakeTransport(response),
    )

    result = adapter.collect(_request())

    assert result.normalized_result["series"][0]["points"] == [[int(NOW.timestamp()) + 15, 2.5]]


def _request(service: str = "checkout") -> EvidenceQueryRequest:
    return EvidenceQueryRequest(
        execution_key="a" * 64,
        package_id="common-service",
        package_version=1,
        template_id="prom.service.cpu",
        template_version=1,
        display_name="CPU 使用趋势",
        source_type="PROMETHEUS",
        evidence_type="METRIC_TIMESERIES",
        query_name="prom.service.cpu",
        controlled_query=(
            'rate(process_cpu_seconds_total{environment="${environment}",service="${service}"}[5m])'
        ),
        parameters={"environment": "testing", "service": service},
        window=build_evidence_window(NOW, NOW),
    )


def _matrix_response(series: int) -> dict[str, object]:
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"instance": f"instance-{index}"},
                    "values": [
                        [(NOW - timedelta(minutes=20)).timestamp(), "1.0"],
                        [NOW.timestamp(), "2.0"],
                    ],
                }
                for index in range(series)
            ],
        },
    }
