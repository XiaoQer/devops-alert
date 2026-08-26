from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.alert_sources import MANUAL_SYSTEM_SOURCE_ID
from incident_intelligence.ids import new_id
from incident_intelligence.persistence.models import (
    AlertRow,
    IncidentActivityRow,
    IncidentRow,
    SignalEventRow,
)
from incident_intelligence.services.incident_center import (
    IncidentCenterService,
    IncidentListQuery,
)

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)


def seed_incident(session_factory: sessionmaker[Session], *, identity: str) -> str:
    signal_id = new_id("sig")
    alert_id = new_id("alt")
    incident_id = new_id("inc")
    with session_factory.begin() as session:
        session.add(
            SignalEventRow(
                id=signal_id,
                alert_source_id=MANUAL_SYSTEM_SOURCE_ID,
                source="manual",
                source_event_id=f"overview-{identity}",
                event_type="manual.reported",
                title="支付接口错误率升高",
                summary="支付接口持续返回错误",
                severity="high",
                service="payment-api",
                environment="production",
                observed_at=NOW,
                received_at=NOW,
                facts={"region": "cn-east-1"},
                payload_fingerprint="a" * 64,
                created_at=NOW,
                version=1,
            )
        )
        session.flush()
        session.add(
            AlertRow(
                id=alert_id,
                signal_event_id=signal_id,
                alert_source_id=MANUAL_SYSTEM_SOURCE_ID,
                source="manual",
                source_instance="b" * 64,
                source_alert_key=f"overview-{identity}",
                state="ACTIVE",
                title="支付接口错误率升高",
                severity="high",
                service="payment-api",
                environment="production",
                first_observed_at=NOW,
                last_observed_at=NOW,
                state_changed_at=NOW,
                created_at=NOW,
                version=1,
            )
        )
        session.flush()
        session.add(
            IncidentRow(
                id=incident_id,
                primary_alert_id=alert_id,
                state="DETECTED",
                title="支付接口错误率升高",
                severity="high",
                service="payment-api",
                environment="production",
                detected_at=NOW,
                assignee="manual-api-client",
                claimed_at=NOW,
                state_changed_at=NOW,
                resolved_at=None,
                closed_at=None,
                created_at=NOW,
                version=1,
            )
        )
    return incident_id


def seed_activities(
    session_factory: sessionmaker[Session], incident_id: str, *, count: int
) -> None:
    with session_factory.begin() as session:
        session.add_all(
            [
                IncidentActivityRow(
                    id=new_id("iact"),
                    incident_id=incident_id,
                    kind="NOTE_ADDED",
                    actor="manual-api-client",
                    from_state=None,
                    to_state=None,
                    note_category="CURRENT_FINDING",
                    message=f"排查记录 {index}",
                    resolution_category=None,
                    resolution_actions=None,
                    root_cause=None,
                    incident_version=index + 1,
                    created_at=NOW + timedelta(seconds=index + 1),
                )
                for index in range(count)
            ]
        )


def test_overview_returns_bounded_activities_and_actor_actions(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    incident_id = seed_incident(session_factory, identity="bounded")
    seed_activities(session_factory, incident_id, count=201)
    service = IncidentCenterService(session_factory=session_factory)

    overview = service.get_overview(incident_id, actor="manual-api-client")

    assert overview.state_changed_at == NOW
    assert overview.resolved_at is None
    assert overview.closed_at is None
    assert len(overview.activities) == 200
    assert overview.activities_truncated is True
    assert tuple((item.created_at, item.id) for item in overview.activities) == tuple(
        sorted((item.created_at, item.id) for item in overview.activities)
    )
    assert overview.activities[0].message == "排查记录 0"
    assert "RELEASE" in overview.allowed_actions
    assert overview.allowed_transitions == ("TRIAGING", "INVESTIGATING")
    assert overview.primary_action is not None
    assert overview.primary_action.action == "TRANSITION"
    assert overview.primary_action.target_state == "TRIAGING"


def test_list_uses_latest_business_activity_time(migrated_engine: Engine) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    incident_id = seed_incident(session_factory, identity="list")
    seed_activities(session_factory, incident_id, count=1)
    service = IncidentCenterService(session_factory=session_factory)

    result = service.list_incidents(IncidentListQuery())

    item = next(item for item in result.items if item.id == incident_id)
    assert item.last_activity_at == NOW + timedelta(seconds=1)


def test_overview_query_count_is_constant_for_one_or_two_hundred_activities(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    first_id = seed_incident(session_factory, identity="query-one")
    second_id = seed_incident(session_factory, identity="query-many")
    seed_activities(session_factory, first_id, count=1)
    seed_activities(session_factory, second_id, count=200)
    service = IncidentCenterService(session_factory=session_factory)
    query_count = 0

    def count_query(*args: object, **kwargs: object) -> None:
        nonlocal query_count
        del args, kwargs
        query_count += 1

    event.listen(migrated_engine, "before_cursor_execute", count_query)
    try:
        service.get_overview(first_id, actor="manual-api-client")
        one_count = query_count
        query_count = 0
        service.get_overview(second_id, actor="manual-api-client")
        many_count = query_count
    finally:
        event.remove(migrated_engine, "before_cursor_execute", count_query)

    assert one_count == many_count
    assert one_count <= 6
