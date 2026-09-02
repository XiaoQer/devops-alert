from __future__ import annotations

import json
from collections.abc import Callable
from time import monotonic

from incident_intelligence.adapters.monitoring_http import (
    MonitoringHttpResponse,
    MonitoringHttpTransport,
    MonitoringPermanentError,
    MonitoringRetryableError,
)
from incident_intelligence.services.monitoring_data_sources import (
    ConnectionTestResult,
    MonitoringDataSource,
)

_TIMEOUT_SECONDS = 5
_MAX_RESPONSE_BYTES = 65_536


class HttpMonitoringConnectionTester:
    def __init__(
        self,
        *,
        transport: MonitoringHttpTransport,
        timer: Callable[[], float] = monotonic,
    ) -> None:
        self._transport = transport
        self._timer = timer

    def test(
        self,
        source: MonitoringDataSource,
        *,
        credential: str | None,
    ) -> ConnectionTestResult:
        if source.credential_env_key is not None and credential is None:
            return ConnectionTestResult(
                state="UNAVAILABLE",
                error_code="monitoring_credential_missing",
            )
        method, url, body = _probe(source)
        headers = {"Accept": "application/json"}
        if credential is not None:
            headers["Authorization"] = f"Bearer {credential}"
        started_at = self._timer()
        try:
            response = self._transport.request(
                method,
                url,
                headers=headers,
                form=None,
                json=body,
                timeout_seconds=_TIMEOUT_SECONDS,
                max_response_bytes=_MAX_RESPONSE_BYTES,
            )
            latency_ms = _latency_ms(started_at, self._timer())
            version = _compatible_version(source, response)
        except (MonitoringRetryableError, MonitoringPermanentError) as error:
            return ConnectionTestResult(
                state="UNAVAILABLE",
                latency_ms=_latency_ms(started_at, self._timer()),
                error_code=str(error)[:64],
            )
        if version is None:
            return ConnectionTestResult(
                state="UNAVAILABLE",
                latency_ms=latency_ms,
                error_code="monitoring_protocol_incompatible",
            )
        return ConnectionTestResult(
            state="AVAILABLE",
            latency_ms=latency_ms,
            compatible_version=version,
        )


def _probe(source: MonitoringDataSource) -> tuple[str, str, dict[str, object] | None]:
    if source.source_type == "PROMETHEUS":
        return "GET", f"{source.base_url}/api/v1/status/buildinfo", None
    if source.source_type == "ELASTICSEARCH":
        return "GET", f"{source.base_url}/", None
    graphql_path = source.field_mapping.get("graphql_path", "/graphql")
    return "POST", f"{source.base_url}{graphql_path}", {"query": "query { version }"}


def _compatible_version(
    source: MonitoringDataSource,
    response: MonitoringHttpResponse,
) -> str | None:
    if response.status_code != 200:
        return None
    try:
        payload = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    version: object
    if source.source_type == "PROMETHEUS":
        data = payload.get("data")
        version = (
            data.get("version")
            if payload.get("status") == "success" and isinstance(data, dict)
            else None
        )
    elif source.source_type == "ELASTICSEARCH":
        version_object = payload.get("version")
        version = version_object.get("number") if isinstance(version_object, dict) else None
    else:
        data = payload.get("data")
        version = data.get("version") if isinstance(data, dict) else None
    if not isinstance(version, str) or not version.strip():
        return None
    return version.strip()[:64]


def _latency_ms(started_at: float, finished_at: float) -> int:
    return min(120_000, max(0, round((finished_at - started_at) * 1_000)))
