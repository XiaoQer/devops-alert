from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from secrets import token_urlsafe

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from incident_intelligence.domain.alert_sources import ALERTMANAGER_COMPAT_SOURCE_ID
from incident_intelligence.domain.incident_rules import IncidentRuleConfig, create_rule
from incident_intelligence.domain.incidents import (
    IncidentAlertFact,
    IncidentAlertLink,
    create_incident,
)
from incident_intelligence.main import create_app
from incident_intelligence.persistence.incident_repository import IncidentRepository
from incident_intelligence.persistence.incident_rule_repository import IncidentRuleRepository
from incident_intelligence.persistence.models import AlertLifecycleRow
from incident_intelligence.settings import Settings

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
INCIDENT_ID = "inc_11111111111111111111111111111111"
RULE_ID = "irl_22222222222222222222222222222222"
ALERT_ID = "alt_33333333333333333333333333333333"


@pytest.fixture
def context(migrated_engine: Engine) -> Iterator[tuple[TestClient, dict[str, str]]]:
    _seed(migrated_engine)
    token = token_urlsafe(32)
    settings = Settings(
        database_url="mysql+pymysql://test-client@127.0.0.1/unused",
        api_token=SecretStr(token),
        alertmanager_token=SecretStr(token_urlsafe(32)),
        cloudevents_token=SecretStr(token_urlsafe(32)),
    )
    with TestClient(create_app(settings, engine=migrated_engine)) as client:
        yield client, {"Authorization": f"Bearer {token}"}


def test_incident_api_lists_real_data_and_supports_state_changes(
    context: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = context

    page = client.get("/api/v1/incidents", headers=headers)
    assert page.status_code == 200, page.text
    assert page.json()["items"][0]["id"] == INCIDENT_ID

    detail = client.get(f"/api/v1/incidents/{INCIDENT_ID}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["alerts"][0]["alert_name"] == "HighErrorRate"

    acknowledged = client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/acknowledge",
        json={"expected_version": 1},
        headers={**headers, "Idempotency-Key": "ack-1"},
    )
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["incident"]["state"] == "ACKNOWLEDGED"

    resolved = client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/resolve",
        json={"expected_version": 2, "resolution_summary": "连接池参数已恢复"},
        headers={**headers, "Idempotency-Key": "resolve-1"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["incident"]["state"] == "RESOLVED"


def test_incident_api_requires_auth_idempotency_and_current_version(
    context: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = context
    assert client.get("/api/v1/incidents").status_code == 401
    assert (
        client.post(
            f"/api/v1/incidents/{INCIDENT_ID}/acknowledge",
            headers=headers,
            json={"expected_version": 1},
        ).status_code
        == 400
    )
    stale = client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/resolve",
        headers={**headers, "Idempotency-Key": "stale"},
        json={"expected_version": 99, "resolution_summary": "已恢复"},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "incident_version_conflict"


def _seed(engine: Engine) -> None:
    with Session(engine) as session:
        rule = create_rule(
            rule_id=RULE_ID,
            name="支付链路异常",
            description="测试规则",
            config=IncidentRuleConfig.model_validate(
                {
                    "environment": "production",
                    "group_by": "SERVICE",
                    "window_minutes": 5,
                    "conditions": ({"type": "DISTINCT_ALERT_NAMES_GTE", "threshold": 1},),
                }
            ),
            now=NOW,
        )
        IncidentRuleRepository(session).insert(rule)
        session.add(_alert())
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
                    id=ALERT_ID,
                    alert_name="HighErrorRate",
                    state="ACTIVE",
                    severity="high",
                    first_received_at=NOW,
                ),
            ),
            created_activity_id="iact_77777777777777777777777777777777",
            now=NOW,
        )
        incidents = IncidentRepository(session)
        incidents.insert(change.incident)
        incidents.link_alerts(
            (
                IncidentAlertLink(
                    incident_id=INCIDENT_ID,
                    alert_id=ALERT_ID,
                    incident_rule_version=1,
                    first_trigger_window=True,
                    linked_at=NOW,
                ),
            )
        )
        incidents.append_activities(change.activities)
        session.commit()


def _alert() -> AlertLifecycleRow:
    return AlertLifecycleRow(
        id=ALERT_ID,
        alert_source_id=ALERTMANAGER_COMPAT_SOURCE_ID,
        source_alert_key="checkout-error",
        episode_started_at=NOW,
        alert_name="HighErrorRate",
        summary="错误率升高",
        description="checkout 错误率超过阈值",
        state="ACTIVE",
        severity="high",
        environment="production",
        service="checkout",
        entity_type="SERVICE",
        entity_key="a" * 64,
        entity_display_name="checkout",
        first_observed_at=NOW,
        last_observed_at=NOW,
        first_received_at=NOW,
        last_received_at=NOW,
        resolved_at=None,
        firing_observed=True,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
