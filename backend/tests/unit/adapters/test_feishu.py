from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.error import URLError

import pytest

import incident_intelligence.adapters.feishu as feishu_module
from incident_intelligence.adapters.feishu import (
    FeishuClient,
    FeishuConfig,
    FeishuHttpResponse,
    FeishuPermanentError,
    FeishuRetryableError,
    UrllibFeishuTransport,
)
from incident_intelligence.services.incident_notifications import (
    IncidentNotificationSnapshot,
    render_incident_card,
)

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


@dataclass
class FakeTransport:
    responses: list[FeishuHttpResponse]
    requests: list[dict[str, object]] = field(default_factory=list)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        timeout_seconds: float,
    ) -> FeishuHttpResponse:
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "json": json,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.responses.pop(0)


def test_send_card_uses_cached_tenant_token_and_chat_id() -> None:
    transport = FakeTransport(
        responses=[
            FeishuHttpResponse(
                status_code=200,
                body={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
            ),
            FeishuHttpResponse(
                status_code=200,
                body={"code": 0, "data": {"message_id": "om_root"}},
            ),
            FeishuHttpResponse(
                status_code=200,
                body={"code": 0, "data": {"message_id": "om_second"}},
            ),
        ]
    )
    client = FeishuClient(
        FeishuConfig(app_id="cli_test", app_secret="secret"),
        transport=transport,
        clock=lambda: NOW,
    )

    first = client.send_incident_card(chat_id="oc_test", card={"type": "template"})
    second = client.send_incident_card(chat_id="oc_test", card={"type": "template"})

    assert first.message_id == "om_root"
    assert second.message_id == "om_second"
    assert len([item for item in transport.requests if "tenant_access_token" in item["url"]]) == 1
    request = transport.requests[-1]
    assert request["headers"] == {"Authorization": "Bearer tenant-token"}
    assert request["json"] == {
        "receive_id": "oc_test",
        "msg_type": "interactive",
        "content": '{"type":"template"}',
    }


def test_card_contains_incident_facts_actions_and_no_sensitive_payload() -> None:
    snapshot = IncidentNotificationSnapshot(
        incident_id="inc_11111111111111111111111111111111",
        reference="INC-20260901-001",
        title="production checkout异常",
        state="OPEN",
        severity="high",
        environment="production",
        group_display_name="checkout",
        alert_count=3,
        active_alert_count=2,
        rule_summary="5 分钟内不同 Alertname 至少 2 个",
        version=1,
    )

    serialized = json.dumps(render_incident_card(snapshot), ensure_ascii=False)

    assert "INC-20260901-001" in serialized
    assert "确认事故" in serialized
    assert "解决事故" in serialized
    assert "app_secret" not in serialized.casefold()
    assert "raw_payload" not in serialized.casefold()


def test_update_and_reply_use_the_bound_root_message() -> None:
    transport = FakeTransport(
        responses=[
            _token_response(),
            FeishuHttpResponse(status_code=200, body={"code": 0}),
            FeishuHttpResponse(
                status_code=200,
                body={"code": 0, "data": {"message_id": "om_reply"}},
            ),
        ]
    )
    client = FeishuClient(
        FeishuConfig(app_id="cli_test", app_secret="secret"),
        transport=transport,
        clock=lambda: NOW,
    )

    client.update_incident_card(message_id="om_root", card={"type": "template"})
    reply = client.reply_to_thread(root_message_id="om_root", text="已开始排查")

    assert reply.message_id == "om_reply"
    assert transport.requests[-2]["method"] == "PATCH"
    assert transport.requests[-2]["url"].endswith("/im/v1/messages/om_root")
    assert transport.requests[-1]["url"].endswith("/im/v1/messages/om_root/reply")


@pytest.mark.parametrize("status_code", (429, 503))
def test_rate_limit_and_server_failure_are_retryable(status_code: int) -> None:
    client = FeishuClient(
        FeishuConfig(app_id="cli_test", app_secret="secret"),
        transport=FakeTransport(
            responses=[
                _token_response(),
                FeishuHttpResponse(status_code=status_code, body={"code": 99991663}),
            ]
        ),
        clock=lambda: NOW,
    )

    with pytest.raises(FeishuRetryableError) as captured:
        client.send_incident_card(chat_id="oc_test", card={})

    assert captured.value.error_code == "99991663"


def test_parameter_failure_and_malformed_success_are_permanent() -> None:
    parameter_client = FeishuClient(
        FeishuConfig(app_id="cli_test", app_secret="secret"),
        transport=FakeTransport(
            responses=[
                _token_response(),
                FeishuHttpResponse(status_code=400, body={"code": 230001}),
            ]
        ),
        clock=lambda: NOW,
    )
    malformed_client = FeishuClient(
        FeishuConfig(app_id="cli_test", app_secret="secret"),
        transport=FakeTransport(
            responses=[_token_response(), FeishuHttpResponse(status_code=200, body={"code": 0})]
        ),
        clock=lambda: NOW,
    )

    with pytest.raises(FeishuPermanentError) as parameter_error:
        parameter_client.send_incident_card(chat_id="oc_test", card={})
    with pytest.raises(FeishuPermanentError) as malformed_error:
        malformed_client.send_incident_card(chat_id="oc_test", card={})

    assert parameter_error.value.error_code == "230001"
    assert malformed_error.value.error_code == "invalid_message_response"


def test_tenant_token_refreshes_inside_the_safety_window() -> None:
    current = NOW
    transport = FakeTransport(responses=[_token_response("first", 120), _token_response("second")])
    client = FeishuClient(
        FeishuConfig(app_id="cli_test", app_secret="secret"),
        transport=transport,
        clock=lambda: current,
    )

    assert client.get_tenant_token() == "first"
    current = NOW + timedelta(seconds=61)
    assert client.get_tenant_token() == "second"


def test_urllib_transport_bounds_success_and_classifies_network_failure(monkeypatch) -> None:
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            assert limit == 1_048_577
            return b'{"code": 0}'

    monkeypatch.setattr(feishu_module, "urlopen", lambda request, timeout: Response())
    transport = UrllibFeishuTransport()

    response = transport.request(
        "POST",
        "https://open.feishu.cn/example",
        headers={},
        json={"safe": True},
        timeout_seconds=5,
    )
    assert response == FeishuHttpResponse(status_code=200, body={"code": 0})

    def fail(request, timeout):
        del request, timeout
        raise URLError("offline")

    monkeypatch.setattr(feishu_module, "urlopen", fail)
    with pytest.raises(FeishuRetryableError) as captured:
        transport.request(
            "POST",
            "https://open.feishu.cn/example",
            headers={},
            json={},
            timeout_seconds=5,
        )
    assert captured.value.error_code == "transport_error"


def _token_response(token: str = "tenant-token", expire: int = 7200) -> FeishuHttpResponse:
    return FeishuHttpResponse(
        status_code=200,
        body={"code": 0, "tenant_access_token": token, "expire": expire},
    )
