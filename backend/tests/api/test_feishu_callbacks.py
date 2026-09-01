from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import SecretStr

from incident_intelligence.main import create_app
from incident_intelligence.services.feishu_events import (
    FeishuCallbackRejected,
    FeishuCallbackResult,
)


def test_event_callback_does_not_accept_platform_bearer_token(settings_factory) -> None:
    app = create_app(
        settings_factory(
            incident_workers_enabled=False,
            feishu_verification_token=SecretStr("verification-token"),
        )
    )
    service = RecordingFeishuService(reject=True)
    app.state.feishu_event_service = service

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/integrations/feishu/events",
            content=b"{}",
            headers={"Authorization": "Bearer platform-token"},
        )

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_feishu_callback"
    assert service.event_calls == 1


def test_url_challenge_and_ignored_event_return_bounded_200(settings_factory) -> None:
    app = create_app(
        settings_factory(
            incident_workers_enabled=False,
            feishu_verification_token=SecretStr("verification-token"),
        )
    )
    service = RecordingFeishuService()
    app.state.feishu_event_service = service

    with TestClient(app) as client:
        challenge = client.post("/api/v1/integrations/feishu/events", content=b'{"challenge":1}')
        ignored = client.post("/api/v1/integrations/feishu/events", content=b"{}")

    assert challenge.status_code == 200
    assert challenge.json() == {"outcome": "CHALLENGE", "challenge": "challenge-value"}
    assert ignored.status_code == 200
    assert ignored.json() == {"outcome": "IGNORED", "challenge": None}


def test_card_action_uses_dedicated_callback_without_platform_auth(settings_factory) -> None:
    app = create_app(
        settings_factory(
            incident_workers_enabled=False,
            feishu_verification_token=SecretStr("verification-token"),
        )
    )
    service = RecordingFeishuService()
    app.state.feishu_event_service = service

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/integrations/feishu/card-actions",
            content=b"{}",
        )

    assert response.status_code == 200
    assert response.json()["outcome"] == "ACKNOWLEDGED"
    assert service.action_calls == 1


class RecordingFeishuService:
    def __init__(self, *, reject: bool = False) -> None:
        self.reject = reject
        self.event_calls = 0
        self.action_calls = 0

    def handle_event(self, headers, body: bytes) -> FeishuCallbackResult:
        del headers
        self.event_calls += 1
        if self.reject:
            raise FeishuCallbackRejected("invalid_signature")
        if b"challenge" in body:
            return FeishuCallbackResult(outcome="CHALLENGE", challenge="challenge-value")
        return FeishuCallbackResult(outcome="IGNORED")

    def handle_card_action(self, headers, body: bytes) -> FeishuCallbackResult:
        del headers, body
        self.action_calls += 1
        return FeishuCallbackResult(outcome="ACKNOWLEDGED")
