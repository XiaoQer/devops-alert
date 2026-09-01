from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from incident_intelligence.adapters.feishu import FeishuMessageResult
from incident_intelligence.domain.incident_rules import IncidentRuleConfig, create_rule
from incident_intelligence.domain.incidents import IncidentAlertFact, create_incident
from incident_intelligence.persistence.incident_repository import (
    IncidentNotificationRecord,
    IncidentNotificationRepository,
    IncidentNotificationRouteRecord,
    IncidentNotificationRouteRepository,
    IncidentRepository,
)
from incident_intelligence.persistence.incident_rule_repository import IncidentRuleRepository
from incident_intelligence.persistence.models import IncidentFeishuThreadRow
from incident_intelligence.persistence.session import make_session_factory
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.incident_notifications import IncidentNotificationService

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
INCIDENT_ID = "inc_11111111111111111111111111111111"
RULE_ID = "irl_22222222222222222222222222222222"
ACTIVITY_ID = "iact_33333333333333333333333333333333"
NOTIFICATION_ID = "ino_44444444444444444444444444444444"
ROUTE_ID = "inr_55555555555555555555555555555555"


def test_create_card_uses_environment_route_and_binds_root_message(
    migrated_engine: Engine,
) -> None:
    _seed(migrated_engine, with_route=True)
    client = RecordingFeishuClient()
    service = _service(migrated_engine, client)

    result = service.deliver(NOTIFICATION_ID)

    assert result.skipped is False
    assert result.message_id == "om_created"
    assert len(client.sent) == 1
    assert client.sent[0][0] == "oc_production"
    assert client.sent[0][1]["header"]["title"]["content"] == "INC-20260901-001"
    with Session(migrated_engine) as session:
        binding = session.scalar(select(IncidentFeishuThreadRow))
        assert binding is not None
        assert binding.incident_id == INCIDENT_ID
        assert binding.root_message_id == "om_created"


def test_alert_link_update_refreshes_root_card_without_thread_spam(
    migrated_engine: Engine,
) -> None:
    _seed(migrated_engine, with_route=True, with_thread=True, kind="UPDATE_CARD")
    client = RecordingFeishuClient()
    service = _service(migrated_engine, client)

    result = service.deliver(NOTIFICATION_ID)

    assert result.message_id == "om_existing"
    assert [item[0] for item in client.updated] == ["om_existing"]
    assert client.replies == []


def test_missing_environment_route_is_a_successful_skip_without_feishu_call(
    migrated_engine: Engine,
) -> None:
    _seed(migrated_engine, with_route=False)
    client = RecordingFeishuClient()
    service = _service(migrated_engine, client)

    result = service.deliver(NOTIFICATION_ID)

    assert result.skipped is True
    assert result.message_id is None
    assert client.sent == []
    assert client.updated == []
    assert client.replies == []


class RecordingFeishuClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, object]]] = []
        self.updated: list[tuple[str, dict[str, object]]] = []
        self.replies: list[tuple[str, str]] = []

    def send_incident_card(
        self,
        *,
        chat_id: str,
        card: dict[str, object],
    ) -> FeishuMessageResult:
        self.sent.append((chat_id, card))
        return FeishuMessageResult(message_id="om_created")

    def update_incident_card(self, *, message_id: str, card: dict[str, object]) -> None:
        self.updated.append((message_id, card))

    def reply_to_thread(self, *, root_message_id: str, text: str) -> FeishuMessageResult:
        self.replies.append((root_message_id, text))
        return FeishuMessageResult(message_id="om_reply")


def _service(engine: Engine, client: RecordingFeishuClient) -> IncidentNotificationService:
    return IncidentNotificationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(make_session_factory(engine)),
        feishu_client=client,
        clock=lambda: NOW,
        id_factory=lambda prefix: "ift_66666666666666666666666666666666",
    )


def _seed(
    engine: Engine,
    *,
    with_route: bool,
    with_thread: bool = False,
    kind: str = "CREATE_CARD",
) -> None:
    with Session(engine) as session:
        rule = create_rule(
            rule_id=RULE_ID,
            name="支付链路异常",
            description="生产支付告警形成 Incident",
            config=IncidentRuleConfig.model_validate(
                {
                    "environment": "production",
                    "group_by": "SERVICE",
                    "window_minutes": 5,
                    "conditions": ({"type": "ACTIVE_ALERTS_GTE", "threshold": 1},),
                }
            ),
            now=NOW,
        )
        IncidentRuleRepository(session).insert(rule)
        change = create_incident(
            incident_id=INCIDENT_ID,
            reference="INC-20260901-001",
            rule_id=RULE_ID,
            rule_version=1,
            environment="production",
            group_by="SERVICE",
            group_key="checkout",
            group_display_name="checkout",
            alerts=(
                IncidentAlertFact(
                    id="alt_77777777777777777777777777777777",
                    alert_name="HighErrorRate",
                    state="ACTIVE",
                    severity="high",
                    first_received_at=NOW,
                ),
            ),
            created_activity_id=ACTIVITY_ID,
            now=NOW,
        )
        incidents = IncidentRepository(session)
        incidents.insert(change.incident)
        incidents.append_activities(change.activities)
        notification = IncidentNotificationRecord(
            id=NOTIFICATION_ID,
            incident_id=INCIDENT_ID,
            activity_id=ACTIVITY_ID,
            notification_key="8" * 64,
            kind=kind,  # type: ignore[arg-type]
            state="LEASED",
            payload={
                "activity_kind": ("ALERTS_LINKED" if kind == "UPDATE_CARD" else "INCIDENT_CREATED"),
                "activity_summary": "关联了新的告警",
            },
            attempt_count=1,
            available_at=NOW,
            lease_owner="worker-1",
            lease_expires_at=NOW,
            last_error_code=None,
            feishu_message_id=None,
            created_at=NOW,
            updated_at=NOW,
        )
        IncidentNotificationRepository(session).enqueue(notification)
        if with_route:
            route = IncidentNotificationRouteRecord(
                id=ROUTE_ID,
                environment="production",
                chat_id="oc_production",
                chat_name="生产事故群",
                enabled=True,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
            IncidentNotificationRouteRepository(session).insert(route)
            if with_thread:
                session.add(
                    IncidentFeishuThreadRow(
                        id="ift_99999999999999999999999999999999",
                        incident_id=INCIDENT_ID,
                        route_id=ROUTE_ID,
                        chat_id="oc_production",
                        root_message_id="om_existing",
                        last_synced_at=NOW,
                        last_error_code=None,
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
        session.commit()
