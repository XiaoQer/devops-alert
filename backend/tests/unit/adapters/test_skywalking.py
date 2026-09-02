from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from incident_intelligence.adapters.monitoring_http import (
    MonitoringHttpResponse,
    MonitoringPermanentError,
)
from incident_intelligence.adapters.skywalking import SkyWalkingEvidenceAdapter
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


class _RawTransport:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def request(self, *args: object, **kwargs: object) -> MonitoringHttpResponse:
        return MonitoringHttpResponse(status_code=200, headers={}, body=self.body)


def test_skywalking_uses_graphql_variables_instead_of_interpolating_service() -> None:
    service = 'checkout"} mutation { unsafe }'
    transport = _FakeTransport({"data": {"execExpression": {"results": []}}})
    adapter = SkyWalkingEvidenceAdapter(
        base_url="http://skywalking:12800",
        graphql_path="/graphql",
        transport=transport,
    )

    adapter.collect(_request("sw.service.health", service=service))

    body = transport.requests[0]["json"]
    assert isinstance(body, dict)
    assert service not in body["query"]
    assert body["variables"]["entity"]["serviceName"] == service
    assert body["variables"]["duration"]["step"] == "MINUTE"


def test_skywalking_caps_trace_and_span_summaries() -> None:
    traces = [
        {
            "traceId": f"trace-{trace_index}",
            "endpoint": "/checkout",
            "duration": 1200,
            "error": True,
            "spans": [
                {
                    "spanId": span_index,
                    "service": "checkout",
                    "endpoint": "SELECT",
                    "duration": 10,
                    "error": span_index == 0,
                }
                for span_index in range(110)
            ],
        }
        for trace_index in range(25)
    ]
    adapter = SkyWalkingEvidenceAdapter(
        base_url="http://skywalking:12800",
        graphql_path="/graphql",
        transport=_FakeTransport({"data": {"queryBasicTraces": {"traces": traces}}}),
    )

    result = adapter.collect(_request("sw.http.failed-traces"))

    assert result.state == "SUCCEEDED"
    assert len(result.normalized_result["traces"]) == 20
    assert len(result.normalized_result["traces"][0]["spans"]) == 100
    assert result.normalized_result["truncated"] is True


def test_skywalking_graphql_errors_are_safe_failure() -> None:
    adapter = SkyWalkingEvidenceAdapter(
        base_url="http://skywalking:12800",
        graphql_path="/graphql",
        transport=_FakeTransport({"errors": [{"message": "database password=unsafe-secret"}]}),
    )

    result = adapter.collect(_request("sw.service.health"))

    assert result.state == "FAILED"
    assert result.error_code == "skywalking_graphql_error"
    assert "unsafe-secret" not in repr(result)


def test_skywalking_supports_endpoint_topology_empty_and_missing_target_results() -> None:
    endpoint = SkyWalkingEvidenceAdapter(
        base_url="http://skywalking:12800",
        graphql_path="/graphql",
        transport=_FakeTransport({"data": {"getEndpoints": [{"id": "1", "name": "/pay"}]}}),
    ).collect(_request("sw.http.endpoints", controlled_query="endpoint_ranking"))
    topology = SkyWalkingEvidenceAdapter(
        base_url="http://skywalking:12800",
        graphql_path="/graphql",
        transport=_FakeTransport({"data": {"getServiceTopologyByName": {"nodes": []}}}),
    ).collect(_request("sw.mysql.dependencies", controlled_query="mysql_dependency_ranking"))
    empty_trace = SkyWalkingEvidenceAdapter(
        base_url="http://skywalking:12800",
        graphql_path="/graphql",
        transport=_FakeTransport({"data": {"queryBasicTraces": {"traces": []}}}),
    ).collect(_request("sw.http.failed-traces"))
    missing = SkyWalkingEvidenceAdapter(
        base_url="http://skywalking:12800",
        graphql_path="/graphql",
        transport=_FakeTransport({}),
    ).collect(
        _request("sw.service.health").model_copy(update={"pre_result_state": "MISSING_TARGET"})
    )

    assert endpoint.state == "SUCCEEDED"
    assert topology.state == "SUCCEEDED"
    assert empty_trace.state == "NO_DATA"
    assert missing.state == "MISSING_TARGET"


def test_skywalking_rejects_unsafe_path_and_unknown_template() -> None:
    with pytest.raises(MonitoringPermanentError, match="skywalking_graphql_path_invalid"):
        SkyWalkingEvidenceAdapter(
            base_url="http://skywalking:12800",
            graphql_path="unsafe",
            transport=_FakeTransport({}),
        )
    adapter = SkyWalkingEvidenceAdapter(
        base_url="http://skywalking:12800",
        graphql_path="/graphql",
        transport=_FakeTransport({}),
    )
    with pytest.raises(MonitoringPermanentError, match="skywalking_template_unsupported"):
        adapter.collect(_request("sw.unknown", controlled_query="unknown"))


@pytest.mark.parametrize("body", (b"not-json", b"[]", b'{"data": null}'))
def test_skywalking_rejects_invalid_response_contracts(body: bytes) -> None:
    adapter = SkyWalkingEvidenceAdapter(
        base_url="http://skywalking:12800",
        graphql_path="/graphql",
        transport=_RawTransport(body),
    )

    with pytest.raises(MonitoringPermanentError):
        adapter.collect(_request("sw.service.health"))


def _request(
    query_name: str,
    *,
    service: str = "checkout",
    controlled_query: str | None = None,
) -> EvidenceQueryRequest:
    evidence_type = "TRACE_SUMMARY" if query_name.endswith("failed-traces") else "METRIC_COMPARISON"
    return EvidenceQueryRequest(
        execution_key="c" * 64,
        package_id="http" if query_name.endswith("failed-traces") else "common-service",
        package_version=1,
        template_id=query_name,
        template_version=1,
        display_name="SkyWalking 证据",
        source_type="SKYWALKING",
        evidence_type=evidence_type,
        query_name=query_name,
        controlled_query=controlled_query
        or ("failed_traces" if query_name.endswith("failed-traces") else "service_health"),
        parameters={"environment": "testing", "service": service},
        window=build_evidence_window(NOW, NOW),
    )
