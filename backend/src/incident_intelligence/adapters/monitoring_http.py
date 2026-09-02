from __future__ import annotations

import json as json_library
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class MonitoringRetryableError(RuntimeError):
    pass


class MonitoringPermanentError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MonitoringHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class MonitoringHttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        form: Mapping[str, str] | None,
        json: Mapping[str, object] | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> MonitoringHttpResponse: ...


class _ReadableResponse(Protocol):
    headers: Mapping[str, str]

    def __enter__(self) -> _ReadableResponse: ...

    def __exit__(self, *args: object) -> object: ...

    def read(self, amount: int) -> bytes: ...

    def getcode(self) -> int: ...


class UrllibMonitoringTransport:
    def __init__(
        self,
        *,
        opener: Callable[[Request, float], _ReadableResponse] | None = None,
    ) -> None:
        self._opener = opener or _open_url

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        form: Mapping[str, str] | None,
        json: Mapping[str, object] | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> MonitoringHttpResponse:
        if form is not None and json is not None:
            raise MonitoringPermanentError("ambiguous_request_body")
        request_headers = dict(headers)
        data: bytes | None = None
        if form is not None:
            data = urlencode(form).encode()
            request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif json is not None:
            data = json_library.dumps(
                json,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
            request_headers.setdefault("Content-Type", "application/json")
        request = Request(url, data=data, headers=request_headers, method=method)
        try:
            with self._opener(request, timeout_seconds) as response:
                body = response.read(max_response_bytes + 1)
                if len(body) > max_response_bytes:
                    raise MonitoringPermanentError("response_too_large")
                return MonitoringHttpResponse(
                    status_code=response.getcode(),
                    headers={key.casefold(): value for key, value in response.headers.items()},
                    body=body,
                )
        except HTTPError as error:
            _raise_http_error(error.code)
        except TimeoutError:
            raise MonitoringRetryableError("network_timeout") from None
        except URLError:
            raise MonitoringRetryableError("network_unavailable") from None
        raise MonitoringPermanentError("unexpected_http_state")


def _open_url(request: Request, timeout: float) -> _ReadableResponse:
    return cast(_ReadableResponse, urlopen(request, timeout=timeout))


def _raise_http_error(status_code: int) -> None:
    if status_code == 429:
        raise MonitoringRetryableError("http_rate_limited")
    if status_code >= 500:
        raise MonitoringRetryableError("http_server_error")
    if status_code in {401, 403}:
        raise MonitoringPermanentError("http_authentication_failed")
    raise MonitoringPermanentError("http_client_error")
