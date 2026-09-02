from __future__ import annotations

import json
import re

from incident_intelligence.adapters.monitoring_http import (
    AdapterEvidenceResult,
    MonitoringHttpTransport,
    MonitoringPermanentError,
)
from incident_intelligence.services.evidence_planning import EvidenceQueryRequest

_SAFE_GRAPHQL_PATH = re.compile(r"^/[a-zA-Z0-9/_-]{1,255}$")

_SERVICE_HEALTH_QUERY = """
query ServiceHealth($expression: String!, $entity: Entity!, $duration: Duration!) {
  execExpression(expression: $expression, entity: $entity, duration: $duration) {
    type
    results { values { id value } }
  }
}
""".strip()

_ENDPOINT_QUERY = """
query ServiceEndpoints($service: ServiceCondition!, $duration: Duration!) {
  getEndpoints(service: $service, duration: $duration) { id name }
}
""".strip()

_TOPOLOGY_QUERY = """
query ServiceTopology($service: ServiceCondition!, $duration: Duration!) {
  getServiceTopologyByName(service: $service, duration: $duration) {
    nodes { id name type isReal }
    calls { id source target detectPoints }
  }
}
""".strip()

_TRACE_QUERY = """
query FailedTraces($condition: TraceQueryCondition!) {
  queryBasicTraces(condition: $condition) {
    traces { traceId endpoint duration error spans }
  }
}
""".strip()


class SkyWalkingEvidenceAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        graphql_path: str,
        transport: MonitoringHttpTransport,
        credential: str | None = None,
        timeout_seconds: float = 10,
        max_response_bytes: int = 4_000_000,
    ) -> None:
        if not _SAFE_GRAPHQL_PATH.fullmatch(graphql_path):
            raise MonitoringPermanentError("skywalking_graphql_path_invalid")
        self._base_url = base_url.rstrip("/")
        self._graphql_path = graphql_path
        self._transport = transport
        self._credential = credential
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    def collect(self, request: EvidenceQueryRequest) -> AdapterEvidenceResult:
        if request.source_type != "SKYWALKING":
            raise MonitoringPermanentError("skywalking_request_type_invalid")
        service = request.parameters.get("service")
        if request.pre_result_state == "MISSING_TARGET" or service is None:
            return AdapterEvidenceResult(
                state="MISSING_TARGET",
                interpretation="缺少服务标识。未执行 SkyWalking 查询。",
            )
        query, variables = _graphql_request(request, service)
        headers = {"Accept": "application/json"}
        if self._credential is not None:
            headers["Authorization"] = f"Bearer {self._credential}"
        response = self._transport.request(
            "POST",
            f"{self._base_url}{self._graphql_path}",
            headers=headers,
            form=None,
            json={"query": query, "variables": variables},
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
        )
        return _parse_response(response.body, request.controlled_query)


def _graphql_request(
    request: EvidenceQueryRequest,
    service: str,
) -> tuple[str, dict[str, object]]:
    duration = {
        "start": request.window.baseline_start.strftime("%Y-%m-%d %H%M"),
        "end": request.window.fault_end.strftime("%Y-%m-%d %H%M"),
        "step": "MINUTE",
    }
    service_condition = {"name": service, "normal": True}
    if request.controlled_query == "service_health":
        return _SERVICE_HEALTH_QUERY, {
            "expression": "avg(service_sla)",
            "entity": {"serviceName": service, "normal": True},
            "duration": duration,
        }
    if request.controlled_query == "endpoint_ranking":
        return _ENDPOINT_QUERY, {
            "service": service_condition,
            "duration": duration,
        }
    if request.controlled_query in {"mysql_dependency_ranking", "service_topology"}:
        return _TOPOLOGY_QUERY, {
            "service": service_condition,
            "duration": duration,
        }
    if request.controlled_query == "failed_traces":
        return _TRACE_QUERY, {
            "condition": {
                "serviceName": service,
                "traceState": "ERROR",
                "queryOrder": "BY_START_TIME",
                "paging": {"pageNum": 1, "pageSize": 20},
                "duration": duration,
            }
        }
    raise MonitoringPermanentError("skywalking_template_unsupported")


def _parse_response(body: bytes, query_kind: str) -> AdapterEvidenceResult:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MonitoringPermanentError("invalid_json_response") from None
    if not isinstance(payload, dict):
        raise MonitoringPermanentError("skywalking_response_invalid")
    if payload.get("errors"):
        return AdapterEvidenceResult(
            state="FAILED",
            error_code="skywalking_graphql_error",
            interpretation="SkyWalking 返回查询错误。",
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MonitoringPermanentError("skywalking_response_invalid")
    if query_kind == "failed_traces":
        return _normalize_traces(data)
    if query_kind == "service_health":
        value = _bounded_json(data.get("execExpression"), list_limit=240)
        return _generic_result("metrics", value, "已获取服务链路指标。")
    if query_kind == "endpoint_ranking":
        value = _bounded_json(data.get("getEndpoints"), list_limit=20)
        return _generic_result("endpoints", value, "已获取服务端点清单。")
    value = _bounded_json(data.get("getServiceTopologyByName"), list_limit=20)
    return _generic_result("topology", value, "已获取服务依赖拓扑。")


def _normalize_traces(data: dict[str, object]) -> AdapterEvidenceResult:
    block = data.get("queryBasicTraces")
    if not isinstance(block, dict) or not isinstance(block.get("traces"), list):
        raise MonitoringPermanentError("skywalking_trace_response_invalid")
    raw_traces = block["traces"]
    traces = [_trace_summary(trace) for trace in raw_traces[:20]]
    traces = [trace for trace in traces if trace is not None]
    if not traces:
        return AdapterEvidenceResult(
            state="NO_DATA",
            normalized_result={"traces": [], "truncated": False},
            interpretation="查询成功。该时间窗口没有失败 Trace。",
        )
    truncated = len(raw_traces) > 20 or any(
        isinstance(trace, dict)
        and isinstance(trace.get("spans"), list)
        and len(trace["spans"]) > 100
        for trace in raw_traces[:20]
    )
    return AdapterEvidenceResult(
        state="SUCCEEDED",
        normalized_result={"traces": traces, "truncated": truncated},
        fault_summary={"trace_count": len(raw_traces), "sample_count": len(traces)},
        interpretation=f"已获取 {len(traces)} 条失败 Trace 摘要。",
    )


def _trace_summary(trace: object) -> dict[str, object] | None:
    if not isinstance(trace, dict):
        return None
    raw_spans = trace.get("spans", [])
    spans = []
    if isinstance(raw_spans, list):
        spans = [_span_summary(span) for span in raw_spans[:100]]
        spans = [span for span in spans if span is not None]
    return {
        "trace_id": str(trace.get("traceId", ""))[:128],
        "endpoint": str(trace.get("endpoint", ""))[:512],
        "duration_ms": _bounded_int(trace.get("duration")),
        "error": bool(trace.get("error", False)),
        "spans": spans,
    }


def _span_summary(span: object) -> dict[str, object] | None:
    if not isinstance(span, dict):
        return None
    return {
        "span_id": _bounded_int(span.get("spanId")),
        "service": str(span.get("service", ""))[:128],
        "endpoint": str(span.get("endpoint", ""))[:512],
        "duration_ms": _bounded_int(span.get("duration")),
        "error": bool(span.get("error", False)),
    }


def _generic_result(
    key: str,
    value: object,
    interpretation: str,
) -> AdapterEvidenceResult:
    if value in (None, [], {}):
        return AdapterEvidenceResult(
            state="NO_DATA",
            normalized_result={key: value},
            interpretation="查询成功。该时间窗口没有匹配数据。",
        )
    return AdapterEvidenceResult(
        state="SUCCEEDED",
        normalized_result={key: value},
        interpretation=interpretation,
    )


def _bounded_json(value: object, *, list_limit: int) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value if not isinstance(value, str) else value[:1_000]
    if isinstance(value, list):
        return [_bounded_json(item, list_limit=list_limit) for item in value[:list_limit]]
    if isinstance(value, dict):
        return {
            str(key)[:128]: _bounded_json(item, list_limit=list_limit)
            for key, item in list(value.items())[:30]
        }
    return str(value)[:1_000]


def _bounded_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, min(int(value), 86_400_000))
    return 0
