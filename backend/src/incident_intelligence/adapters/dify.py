from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class DifyRetryableError(RuntimeError):
    pass


class DifyPermanentError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DifyHttpResponse:
    status_code: int
    body: bytes


class DifyHttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, object],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> DifyHttpResponse: ...


class _ReadableResponse(Protocol):
    def __enter__(self) -> _ReadableResponse: ...

    def __exit__(self, *args: object) -> object: ...

    def read(self, amount: int) -> bytes: ...

    def getcode(self) -> int: ...


class UrllibDifyTransport:
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
        json: Mapping[str, object],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> DifyHttpResponse:
        request = Request(
            url,
            data=_json_bytes(json),
            headers={**headers, "Content-Type": "application/json"},
            method=method,
        )
        try:
            with self._opener(request, timeout_seconds) as response:
                body = response.read(max_response_bytes + 1)
                if len(body) > max_response_bytes:
                    raise DifyPermanentError("dify_response_too_large")
                return DifyHttpResponse(status_code=response.getcode(), body=body)
        except HTTPError as error:
            _raise_http_error(error.code)
        except TimeoutError:
            raise DifyRetryableError("dify_timeout") from None
        except URLError:
            raise DifyRetryableError("dify_unavailable") from None
        raise DifyPermanentError("dify_transport_unexpected")


class DifyDiagnosisClient:
    """Calls one configured Dify Workflow; it does not expose arbitrary Dify access."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        transport: DifyHttpTransport,
    ) -> None:
        self._base_url = _base_url(base_url)
        self._api_key = _required_secret(api_key, "dify_api_key_missing")
        self._transport = transport

    def run_workflow(self, diagnosis_run_id: str, *, capability_token: str) -> dict[str, object]:
        response = self._transport.request(
            "POST",
            f"{self._base_url}/v1/workflows/run",
            headers={"Accept": "application/json", "Authorization": f"Bearer {self._api_key}"},
            json={
                "inputs": {
                    "diagnosis_run_id": diagnosis_run_id,
                    "capability_token": capability_token,
                    "output_contract_version": "diagnosis-report.v1",
                },
                "response_mode": "blocking",
                "user": "incident-intelligence",
            },
            timeout_seconds=90,
            max_response_bytes=65_536,
        )
        if response.status_code != 200:
            _raise_http_error(response.status_code)
        return _report_from_response(response.body)


def _base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
        raise ValueError("dify_base_url_invalid")
    if parsed.query or parsed.fragment:
        raise ValueError("dify_base_url_invalid")
    return normalized


def _required_secret(value: str, error_code: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(error_code)
    return normalized


def _json_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _open_url(request: Request, timeout: float) -> _ReadableResponse:
    return cast(_ReadableResponse, urlopen(request, timeout=timeout))


def _raise_http_error(status_code: int) -> None:
    if status_code == 429:
        raise DifyRetryableError("dify_rate_limited")
    if status_code >= 500:
        raise DifyRetryableError("dify_server_error")
    if status_code in {401, 403}:
        raise DifyPermanentError("dify_authentication_failed")
    raise DifyPermanentError("dify_protocol_error")


def _report_from_response(body: bytes) -> dict[str, object]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise DifyPermanentError("dify_protocol_error") from None
    if not isinstance(payload, dict):
        raise DifyPermanentError("dify_protocol_error")
    data = payload.get("data")
    outputs = data.get("outputs") if isinstance(data, dict) else None
    candidate = outputs.get("diagnosis_report") if isinstance(outputs, dict) else None
    if isinstance(candidate, str):
        try:
            candidate = json.loads(_unwrap_json_code_fence(_discard_leading_think_block(candidate)))
        except json.JSONDecodeError:
            raise DifyPermanentError("dify_protocol_error") from None
    if not isinstance(candidate, dict):
        raise DifyPermanentError("dify_protocol_error")
    return cast(dict[str, object], candidate)


def _unwrap_json_code_fence(value: str) -> str:
    normalized = value.strip()
    if not normalized.startswith("```"):
        return normalized
    first_line, separator, remaining = normalized.partition("\n")
    if separator != "\n" or first_line.casefold() not in {"```json", "```"}:
        return normalized
    content, closing_separator, trailing = remaining.rpartition("\n```")
    if closing_separator != "\n```" or trailing:
        return normalized
    return content.strip()


def _discard_leading_think_block(value: str) -> str:
    normalized = value.strip()
    if not normalized.casefold().startswith("<think>"):
        return normalized
    closing_index = normalized.casefold().find("</think>")
    if closing_index < 0:
        return normalized
    return normalized[closing_index + len("</think>") :].strip()
