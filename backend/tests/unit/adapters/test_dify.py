from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from incident_intelligence.adapters.dify import (
    DifyDiagnosisClient,
    DifyHttpResponse,
    DifyRetryableError,
)


def test_fixed_workflow_sends_only_capability_contract_and_returns_structured_report() -> None:
    transport = _Transport(
        DifyHttpResponse(
            status_code=200,
            body=json.dumps(
                {
                    "data": {
                        "outputs": {
                            "diagnosis_report": {
                                "confirmed_facts": [],
                                "hypotheses": [],
                                "references": [],
                                "unknowns": ["暂无可确认事实。"],
                                "suggested_human_actions": ["请人工继续核对。"],
                            }
                        }
                    }
                }
            ).encode(),
        )
    )
    client = DifyDiagnosisClient(
        base_url="https://dify.example.test",
        api_key="test-api-key",
        transport=transport,
    )

    report = client.run_workflow(
        f"drun_{'1' * 32}",
        capability_token="short-lived-capability",
    )

    assert report["unknowns"] == ["暂无可确认事实。"]
    assert transport.url == "https://dify.example.test/v1/workflows/run"
    assert transport.headers["Authorization"] == "Bearer test-api-key"
    assert transport.json == {
        "inputs": {
            "diagnosis_run_id": f"drun_{'1' * 32}",
            "capability_token": "short-lived-capability",
            "output_contract_version": "diagnosis-report.v1",
        },
        "response_mode": "blocking",
        "user": "incident-intelligence",
    }


def test_rate_limited_workflow_is_retryable_without_exposing_provider_body() -> None:
    client = DifyDiagnosisClient(
        base_url="https://dify.example.test",
        api_key="test-api-key",
        transport=_Transport(DifyHttpResponse(status_code=429, body=b"provider secret body")),
    )

    with pytest.raises(DifyRetryableError, match="dify_rate_limited"):
        client.run_workflow(f"drun_{'1' * 32}", capability_token="short-lived-capability")


class _Transport:
    def __init__(self, response: DifyHttpResponse) -> None:
        self._response = response
        self.url = ""
        self.headers: Mapping[str, str] = {}
        self.json: Mapping[str, object] | None = None

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
        assert method == "POST"
        assert timeout_seconds == 90
        assert max_response_bytes == 65_536
        self.url = url
        self.headers = headers
        self.json = json
        return self._response
