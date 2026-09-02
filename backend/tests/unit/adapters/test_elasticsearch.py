from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from incident_intelligence.adapters.elasticsearch import ElasticsearchEvidenceAdapter
from incident_intelligence.adapters.monitoring_http import (
    MonitoringHttpResponse,
    MonitoringPermanentError,
)
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


def test_elasticsearch_uses_fixed_filters_and_whitelisted_source_fields() -> None:
    transport = _FakeTransport(_response())
    adapter = ElasticsearchEvidenceAdapter(
        base_url="http://elasticsearch:9200",
        index="logs-*",
        field_mapping=_field_mapping(),
        transport=transport,
    )

    result = adapter.collect(_request())

    sent = transport.requests[0]
    assert sent["method"] == "POST"
    assert sent["url"] == "http://elasticsearch:9200/logs-*/_search"
    body = sent["json"]
    assert isinstance(body, dict)
    assert body["size"] == 50
    assert set(body["_source"]) == {
        "@timestamp",
        "service.name",
        "environment",
        "log.level",
        "message",
        "error.type",
        "trace.id",
        "host.name",
    }
    filters = body["query"]["bool"]["filter"]
    assert {"term": {"service.name": "checkout"}} in filters
    assert {"term": {"environment": "testing"}} in filters
    assert result.state == "SUCCEEDED"
    sample = result.normalized_result["samples"][0]
    assert "secret-token" not in sample["message"]
    assert len(sample["fingerprint"]) == 64


def test_elasticsearch_caps_untrusted_response_to_fifty_samples() -> None:
    response = _response()
    response["hits"]["hits"] = response["hits"]["hits"] * 60
    adapter = ElasticsearchEvidenceAdapter(
        base_url="http://elasticsearch:9200",
        index="logs-*",
        field_mapping=_field_mapping(),
        transport=_FakeTransport(response),
    )

    result = adapter.collect(_request())

    assert len(result.normalized_result["samples"]) == 50
    assert result.normalized_result["truncated"] is True


def test_elasticsearch_returns_bounded_empty_and_missing_target_results() -> None:
    adapter = ElasticsearchEvidenceAdapter(
        base_url="http://elasticsearch:9200",
        index="logs-*",
        field_mapping=_field_mapping(),
        transport=_FakeTransport({"hits": {"total": 0, "hits": []}}),
    )

    empty = adapter.collect(_request())
    missing = adapter.collect(_request().model_copy(update={"pre_result_state": "MISSING_TARGET"}))

    assert empty.state == "NO_DATA"
    assert missing.state == "MISSING_TARGET"


@pytest.mark.parametrize(
    ("index", "mapping", "error_code"),
    (
        ("unsafe index", None, "elasticsearch_index_invalid"),
        ("logs-*", {"timestamp": "@timestamp"}, "elasticsearch_mapping_incomplete"),
    ),
)
def test_elasticsearch_rejects_unsafe_local_configuration(
    index: str,
    mapping: dict[str, str] | None,
    error_code: str,
) -> None:
    with pytest.raises(MonitoringPermanentError, match=error_code):
        ElasticsearchEvidenceAdapter(
            base_url="http://elasticsearch:9200",
            index=index,
            field_mapping=_field_mapping() if mapping is None else mapping,
            transport=_FakeTransport({}),
        )


@pytest.mark.parametrize(
    "body",
    (b"not-json", b"[]", b'{"hits": {"hits": "invalid"}}'),
)
def test_elasticsearch_rejects_invalid_responses(body: bytes) -> None:
    adapter = ElasticsearchEvidenceAdapter(
        base_url="http://elasticsearch:9200",
        index="logs-*",
        field_mapping=_field_mapping(),
        transport=_RawTransport(body),
    )

    with pytest.raises(MonitoringPermanentError):
        adapter.collect(_request())


def test_elasticsearch_ignores_invalid_hits_and_uses_total_fallback() -> None:
    response = _response()
    response["hits"]["hits"].insert(0, None)
    response["hits"]["total"] = "unknown"
    result = ElasticsearchEvidenceAdapter(
        base_url="http://elasticsearch:9200",
        index="logs-*",
        field_mapping=_field_mapping(),
        transport=_FakeTransport(response),
    ).collect(_request())

    assert result.normalized_result["total"] == 2


def _request() -> EvidenceQueryRequest:
    return EvidenceQueryRequest(
        execution_key="b" * 64,
        package_id="common-service",
        package_version=1,
        template_id="elk.service.errors",
        template_version=1,
        display_name="错误日志趋势",
        source_type="ELASTICSEARCH",
        evidence_type="LOG_AGGREGATION",
        query_name="elk.service.errors",
        controlled_query="service_error_aggregation",
        parameters={"environment": "testing", "service": "checkout"},
        window=build_evidence_window(NOW, NOW),
    )


def _field_mapping() -> dict[str, str]:
    return {
        "index": "logs-*",
        "timestamp": "@timestamp",
        "service": "service.name",
        "environment": "environment",
        "level": "log.level",
        "message": "message",
        "error_type": "error.type",
        "trace_id": "trace.id",
        "host": "host.name",
    }


def _response() -> dict[str, object]:
    return {
        "hits": {
            "total": {"value": 1, "relation": "eq"},
            "hits": [
                {
                    "_source": {
                        "@timestamp": NOW.isoformat(),
                        "service.name": "checkout",
                        "environment": "testing",
                        "log.level": "ERROR",
                        "message": "Authorization: Bearer secret-token request failed",
                        "error.type": "TimeoutError",
                        "trace.id": "trace-1",
                        "host.name": "checkout-1",
                    }
                }
            ],
        }
    }
