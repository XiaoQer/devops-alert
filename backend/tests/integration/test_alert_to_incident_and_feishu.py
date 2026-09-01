from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from incident_intelligence.adapters.feishu import FeishuMessageResult
from incident_intelligence.domain.incident_rules import (
    IncidentRuleConfig,
    create_rule,
    publish_rule,
    record_successful_dry_run,
)
from incident_intelligence.main import create_app
from incident_intelligence.persistence.incident_repository import (
    IncidentNotificationRouteRecord,
    IncidentNotificationRouteRepository,
)
from incident_intelligence.persistence.incident_rule_repository import IncidentRuleRepository
from incident_intelligence.persistence.models import (
    IncidentFeishuThreadRow,
    OperationalIncidentActivityRow,
    OperationalIncidentRow,
)
from incident_intelligence.persistence.session import make_session_factory
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.incident_notification_runner import IncidentNotificationRunner
from incident_intelligence.services.incident_notifications import IncidentNotificationService
from incident_intelligence.settings import Settings

RULE_ID = "irl_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
ROUTE_ID = "inr_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
VERIFY_TOKEN = "verification-token"


def test_alert_to_incident_feishu_message_and_resolution_flow(
    migrated_engine: Engine,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    _seed_rule_and_route(migrated_engine, now)
    api_token = token_urlsafe(32)
    alertmanager_token = token_urlsafe(32)
    settings = Settings(
        database_url=str(migrated_engine.url),
        api_token=SecretStr(api_token),
        alertmanager_token=SecretStr(alertmanager_token),
        cloudevents_token=SecretStr(token_urlsafe(32)),
        feishu_app_id=SecretStr("cli_test"),
        feishu_app_secret=SecretStr("secret-test"),
        feishu_verification_token=SecretStr(VERIFY_TOKEN),
        incident_workers_enabled=False,
    )
    app = create_app(settings, engine=migrated_engine)
    platform_headers = {"Authorization": f"Bearer {api_token}"}

    with TestClient(app) as client:
        received = client.post(
            "/api/v1/intake/alertmanager",
            headers={"Authorization": f"Bearer {alertmanager_token}"},
            json=_alertmanager_payload(now),
        )
        assert received.status_code == 202, received.text

        evaluated = app.state.incident_evaluation_runner.run_once(limit=10)
        assert evaluated.succeeded == 1
        incident_page = client.get("/api/v1/incidents", headers=platform_headers)
        incident = incident_page.json()["items"][0]
        assert incident["state"] == "OPEN"
        assert incident["alert_count"] == 1

        feishu = RecordingFeishuClient()
        notification_service = IncidentNotificationService(
            uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
            feishu_client=feishu,
        )
        notification_runner = IncidentNotificationRunner(
            uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(migrated_engine)),
            processor=notification_service,
        )
        notified = notification_runner.run_once(limit=10)
        assert notified.succeeded == 1
        assert feishu.root_card_references == [incident["reference"]]

        with Session(migrated_engine) as session:
            thread = session.scalar(select(IncidentFeishuThreadRow))
            assert thread is not None
            root_message_id = thread.root_message_id

        callback = _feishu_message(now, root_message_id)
        raw = json.dumps(callback, ensure_ascii=False, separators=(",", ":")).encode()
        callback_response = client.post(
            "/api/v1/integrations/feishu/events",
            content=raw,
            headers={"Content-Type": "application/json", **_feishu_headers(raw, now)},
        )
        assert callback_response.status_code == 200, callback_response.text
        assert callback_response.json()["outcome"] == "RECORDED"

        resolved = client.post(
            f"/api/v1/incidents/{incident['id']}/resolve",
            headers={**platform_headers, "Idempotency-Key": "resolve-e2e"},
            json={"expected_version": 1, "resolution_summary": "连接池参数已恢复"},
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["incident"]["state"] == "RESOLVED"

    with Session(migrated_engine) as session:
        stored = session.get(OperationalIncidentRow, incident["id"])
        assert stored is not None and stored.resolution_summary == "连接池参数已恢复"
        messages = tuple(
            session.scalars(
                select(OperationalIncidentActivityRow.summary).where(
                    OperationalIncidentActivityRow.kind == "FEISHU_MESSAGE_RECORDED"
                )
            )
        )
        assert messages == ("已开始排查数据库连接池",)


class RecordingFeishuClient:
    def __init__(self) -> None:
        self.root_card_references: list[str] = []

    def send_incident_card(self, *, chat_id: str, card: dict[str, object]) -> FeishuMessageResult:
        assert chat_id == "oc_incident"
        header = card["header"]
        assert isinstance(header, dict)
        title = header["title"]
        assert isinstance(title, dict)
        self.root_card_references.append(str(title["content"]))
        return FeishuMessageResult(message_id="om_root")

    def update_incident_card(self, *, message_id: str, card: dict[str, object]) -> None:
        raise AssertionError((message_id, card))

    def reply_to_thread(self, *, root_message_id: str, text: str) -> FeishuMessageResult:
        raise AssertionError((root_message_id, text))


def _seed_rule_and_route(engine: Engine, now: datetime) -> None:
    with Session(engine) as session:
        rule = create_rule(
            rule_id=RULE_ID,
            name="生产 checkout 告警",
            description="一条活动告警创建正式 Incident",
            config=IncidentRuleConfig.model_validate(
                {
                    "environment": "production",
                    "services": ("checkout",),
                    "group_by": "SERVICE",
                    "window_minutes": 5,
                    "conditions": ({"type": "ACTIVE_ALERTS_GTE", "threshold": 1},),
                }
            ),
            now=now - timedelta(minutes=3),
        )
        rule = record_successful_dry_run(
            rule,
            dry_run_id="ird_cccccccccccccccccccccccccccccccc",
            now=now - timedelta(minutes=2),
        )
        IncidentRuleRepository(session).insert(publish_rule(rule, now=now - timedelta(minutes=1)))
        IncidentNotificationRouteRepository(session).insert(
            IncidentNotificationRouteRecord(
                id=ROUTE_ID,
                environment="production",
                chat_id="oc_incident",
                chat_name="生产事故群",
                enabled=True,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()


def _alertmanager_payload(now: datetime) -> dict[str, object]:
    return {
        "version": "4",
        "groupKey": '{}:{alertname="CheckoutHighErrorRate"}',
        "truncatedAlerts": 0,
        "status": "firing",
        "receiver": "incident-intelligence",
        "groupLabels": {"alertname": "CheckoutHighErrorRate"},
        "commonLabels": {},
        "commonAnnotations": {},
        "externalURL": "https://alertmanager.example.com",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "CheckoutHighErrorRate",
                    "severity": "high",
                    "environment": "production",
                    "service": "checkout",
                    "instance": "checkout-1",
                },
                "annotations": {"summary": "checkout 错误率升高", "description": "错误率超过阈值"},
                "startsAt": (now - timedelta(minutes=1)).isoformat(),
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "https://prometheus.example.com/graph",
                "fingerprint": "checkout-high-error-rate",
            }
        ],
    }


def _feishu_message(now: datetime, root_message_id: str) -> dict[str, object]:
    return {
        "schema": "2.0",
        "header": {
            "event_id": "evt_e2e_message",
            "event_type": "im.message.receive_v1",
            "create_time": str(int(now.timestamp() * 1000)),
            "token": VERIFY_TOKEN,
        },
        "event": {
            "sender": {"sender_id": {"open_id": "open_user"}, "sender_type": "user"},
            "message": {
                "message_id": "om_reply",
                "root_id": root_message_id,
                "parent_id": root_message_id,
                "chat_id": "oc_incident",
                "message_type": "text",
                "content": json.dumps(
                    {"text": "@_user_1 已开始排查数据库连接池"},
                    ensure_ascii=False,
                ),
            },
            "mentions": [{"key": "@_user_1", "id": {"open_id": "open_bot"}}],
        },
    }


def _feishu_headers(raw: bytes, now: datetime) -> dict[str, str]:
    timestamp = str(int(now.timestamp()))
    nonce = "nonce-e2e"
    material = timestamp.encode() + nonce.encode() + VERIFY_TOKEN.encode() + raw
    return {
        "X-Lark-Request-Timestamp": timestamp,
        "X-Lark-Request-Nonce": nonce,
        "X-Lark-Signature": sha256(material).hexdigest(),
    }
