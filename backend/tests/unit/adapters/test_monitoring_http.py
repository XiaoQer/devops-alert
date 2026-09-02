from __future__ import annotations

from urllib.error import HTTPError

import pytest

from incident_intelligence.adapters.monitoring_http import (
    MonitoringPermanentError,
    MonitoringRetryableError,
    UrllibMonitoringTransport,
)


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, amount: int) -> bytes:
        return self._body[:amount]

    def getcode(self) -> int:
        return 200

    @property
    def headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}


def test_transport_rejects_response_over_configured_limit() -> None:
    transport = UrllibMonitoringTransport(opener=lambda request, timeout: _Response(b"123456"))

    with pytest.raises(MonitoringPermanentError, match="response_too_large"):
        transport.request(
            "GET",
            "http://monitoring/health",
            headers={},
            form=None,
            json=None,
            timeout_seconds=1,
            max_response_bytes=5,
        )


@pytest.mark.parametrize(
    ("status", "error_type", "code"),
    [
        (429, MonitoringRetryableError, "http_rate_limited"),
        (503, MonitoringRetryableError, "http_server_error"),
        (401, MonitoringPermanentError, "http_authentication_failed"),
        (404, MonitoringPermanentError, "http_client_error"),
    ],
)
def test_transport_classifies_http_status_without_exposing_response(
    status: int,
    error_type: type[Exception],
    code: str,
) -> None:
    def fail(request, timeout):
        raise HTTPError(request.full_url, status, "unsafe upstream text", {}, None)

    transport = UrllibMonitoringTransport(opener=fail)

    with pytest.raises(error_type, match=code):
        transport.request(
            "GET",
            "http://monitoring/health",
            headers={},
            form=None,
            json=None,
            timeout_seconds=1,
            max_response_bytes=100,
        )
