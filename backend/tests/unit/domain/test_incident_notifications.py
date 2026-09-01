from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from incident_intelligence.domain.incident_notifications import (
    IncidentNotificationRoute,
    create_notification_route,
    update_notification_route,
)

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
ROUTE_ID = "inr_11111111111111111111111111111111"


def test_enabled_route_derives_environment_uniqueness_key() -> None:
    route = create_notification_route(
        route_id=ROUTE_ID,
        environment="production",
        chat_id="oc_production_incidents",
        chat_name="生产事故群",
        enabled=True,
        now=NOW,
    )

    assert route.enabled_environment_key == "production"
    assert route.version == 1


def test_disabled_route_releases_environment_uniqueness_key() -> None:
    enabled = create_notification_route(
        route_id=ROUTE_ID,
        environment="production",
        chat_id="oc_production_incidents",
        chat_name="生产事故群",
        enabled=True,
        now=NOW,
    )

    disabled = update_notification_route(
        enabled,
        environment="production",
        chat_id="oc_production_incidents",
        chat_name="生产事故群",
        enabled=False,
        now=NOW,
    )

    assert disabled.enabled_environment_key is None
    assert disabled.version == 2


def test_route_rejects_invalid_environment_and_blank_chat() -> None:
    with pytest.raises(ValidationError):
        IncidentNotificationRoute(
            id=ROUTE_ID,
            environment="Production",
            chat_id=" ",
            chat_name="生产事故群",
            enabled=True,
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
