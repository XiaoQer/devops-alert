from __future__ import annotations

import json
import math
import statistics
from typing import Literal, TypedDict

from incident_intelligence.adapters.monitoring_http import (
    AdapterEvidenceResult,
    MonitoringHttpTransport,
    MonitoringPermanentError,
)
from incident_intelligence.services.evidence_planning import EvidenceQueryRequest


class _NormalizedSeries(TypedDict):
    metric: dict[str, str]
    points: list[list[int | float]]


class PrometheusEvidenceAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        transport: MonitoringHttpTransport,
        credential: str | None = None,
        timeout_seconds: float = 10,
        max_response_bytes: int = 2_000_000,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._credential = credential
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    def collect(self, request: EvidenceQueryRequest) -> AdapterEvidenceResult:
        if request.source_type != "PROMETHEUS":
            raise MonitoringPermanentError("prometheus_request_type_invalid")
        if request.pre_result_state == "MISSING_TARGET":
            return AdapterEvidenceResult(
                state="MISSING_TARGET",
                interpretation="缺少服务标识。未执行 Prometheus 查询。",
            )
        query = _render_controlled_query(request.controlled_query, request.parameters)
        total_seconds = max(
            1,
            int((request.window.fault_end - request.window.baseline_start).total_seconds()),
        )
        step_seconds = max(1, math.ceil(total_seconds / 239))
        headers = {"Accept": "application/json"}
        if self._credential is not None:
            headers["Authorization"] = f"Bearer {self._credential}"
        response = self._transport.request(
            "POST",
            f"{self._base_url}/api/v1/query_range",
            headers=headers,
            form={
                "query": query,
                "start": request.window.baseline_start.isoformat(),
                "end": request.window.fault_end.isoformat(),
                "step": str(step_seconds),
                "limit": "20",
            },
            json=None,
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
        )
        return _parse_matrix(response.body, request)


def _render_controlled_query(template: str, parameters: dict[str, str]) -> str:
    rendered = template
    for key, value in parameters.items():
        safe_value = value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
        rendered = rendered.replace(f"${{{key}}}", safe_value)
    if "${" in rendered:
        raise MonitoringPermanentError("query_parameter_missing")
    return rendered


def _parse_matrix(body: bytes, request: EvidenceQueryRequest) -> AdapterEvidenceResult:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MonitoringPermanentError("invalid_json_response") from None
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise MonitoringPermanentError("prometheus_query_failed")
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("resultType") != "matrix":
        raise MonitoringPermanentError("prometheus_result_type_invalid")
    raw_series = data.get("result")
    if not isinstance(raw_series, list):
        raise MonitoringPermanentError("prometheus_result_invalid")
    if len(raw_series) > 20:
        raise MonitoringPermanentError("series_limit_exceeded")

    series: list[_NormalizedSeries] = []
    baseline_values: list[float] = []
    fault_values: list[float] = []
    for raw_item in raw_series:
        normalized = _normalize_series(raw_item)
        series.append(normalized)
        for raw_point in normalized["points"]:
            timestamp = int(raw_point[0])
            value = float(raw_point[1])
            if timestamp < request.window.fault_start.timestamp():
                baseline_values.append(value)
            else:
                fault_values.append(value)
    if not series or not any(item["points"] for item in series):
        return AdapterEvidenceResult(
            state="NO_DATA",
            normalized_result={"series": []},
            interpretation="查询成功。该时间窗口没有指标数据。",
        )
    state: Literal["SUCCEEDED", "INSUFFICIENT_BASELINE"] = (
        "SUCCEEDED" if baseline_values else "INSUFFICIENT_BASELINE"
    )
    return AdapterEvidenceResult(
        state=state,
        normalized_result={"series": series},
        baseline_summary=_summary(baseline_values),
        fault_summary=_summary(fault_values),
        interpretation=(
            "已获取故障前基线和故障期指标。"
            if baseline_values
            else "已获取故障期指标。基线窗口没有有效数据。"
        ),
    )


def _normalize_series(raw_item: object) -> _NormalizedSeries:
    if not isinstance(raw_item, dict):
        raise MonitoringPermanentError("prometheus_series_invalid")
    raw_metric = raw_item.get("metric", {})
    raw_values = raw_item.get("values", [])
    if not isinstance(raw_metric, dict) or not isinstance(raw_values, list):
        raise MonitoringPermanentError("prometheus_series_invalid")
    metric = {str(key)[:128]: str(value)[:256] for key, value in list(raw_metric.items())[:30]}
    points: list[list[int | float]] = []
    for raw_point in raw_values[:240]:
        if not isinstance(raw_point, list) or len(raw_point) != 2:
            raise MonitoringPermanentError("prometheus_point_invalid")
        try:
            timestamp = int(float(raw_point[0]))
            value = float(raw_point[1])
        except (TypeError, ValueError, OverflowError):
            raise MonitoringPermanentError("prometheus_point_invalid") from None
        if math.isfinite(value):
            points.append([timestamp, value])
    return {"metric": metric, "points": points}


def _summary(
    values: list[float],
) -> dict[str, str | int | float | bool | None]:
    if not values:
        return {"sample_count": 0, "average": None, "maximum": None, "median": None}
    return {
        "sample_count": len(values),
        "average": sum(values) / len(values),
        "maximum": max(values),
        "median": statistics.median(values),
    }
