from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import column, insert, inspect, select, table
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from incident_intelligence.domain.alert_sources import ALERTMANAGER_COMPAT_SOURCE_ID
from incident_intelligence.ids import new_id
from incident_intelligence.persistence.models import IncidentRow

from .test_constraints import NOW, make_alert, make_signal

EVENT_TABLES = {
    "alert_event_profiles",
    "alert_event_membership_decisions",
    "alert_event_operations",
    "alert_event_lifecycle_jobs",
}
EVENT_COLUMNS = {
    "profile_version",
    "forming_until",
    "observing_until",
    "closed_at",
    "member_limit",
    "continuation_group_id",
    "pending_count",
}


def test_upgrade_adds_alert_event_schema_and_matches_orm(
    alembic_config: Config,
    mysql_engine: Engine,
) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    try:
        inspector = inspect(mysql_engine)
        assert set(inspector.get_table_names()) >= EVENT_TABLES
        assert {item["name"] for item in inspector.get_columns("alert_groups")} >= EVENT_COLUMNS
        command.check(alembic_config)
    finally:
        command.downgrade(alembic_config, "base")


def test_alert_event_schema_downgrades_cleanly(
    alembic_config: Config,
    mysql_engine: Engine,
) -> None:
    command.downgrade(alembic_config, "base")


def test_upgrade_preserves_existing_members_and_incident_links(
    alembic_config: Config,
    mysql_engine: Engine,
) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "0009_alert_source_environment")
    group_id = new_id("agr")
    with Session(mysql_engine) as session:
        signal = make_signal(
            alert_source_id=ALERTMANAGER_COMPAT_SOURCE_ID,
            source_event_id=new_id("sig"),
        )
        session.add(signal)
        session.flush()
        alert = make_alert(signal.id, source_alert_key=new_id("alt"))
        session.add(alert)
        session.flush()
        incident = IncidentRow(
            id=new_id("inc"),
            primary_alert_id=alert.id,
            state="DETECTED",
            title=alert.title,
            severity=alert.severity,
            service="payment-api",
            environment="production",
            detected_at=NOW,
            assignee=None,
            claimed_at=None,
            state_changed_at=NOW,
            resolved_at=None,
            closed_at=None,
            created_at=NOW,
            version=1,
        )
        session.add(incident)
        session.flush()
        incident_id = incident.id
        groups = table(
            "alert_groups",
            *[
                column(name)
                for name in (
                    "id",
                    "state",
                    "storm_state",
                    "rule_version",
                    "service",
                    "entity_type",
                    "entity_key",
                    "entity_display_name",
                    "problem_key",
                    "problem_type",
                    "scope_type",
                    "scope_key",
                    "scope_display_name",
                    "signature_version",
                    "environment",
                    "symptom",
                    "title",
                    "severity",
                    "representative_alert_id",
                    "incident_id",
                    "first_observed_at",
                    "last_observed_at",
                    "state_changed_at",
                    "last_member_at",
                    "active_count",
                    "total_count",
                    "impacted_resource_count",
                    "desired_correlation_version",
                    "reason_codes",
                    "explanation",
                    "created_at",
                    "updated_at",
                    "version",
                )
            ],
        )
        session.execute(
            insert(groups).values(
                id=group_id,
                state="RESOLVED",
                storm_state="NORMAL",
                rule_version="alert-grouping.v1",
                service="payment-api",
                entity_type="SERVICE",
                entity_key="1" * 64,
                entity_display_name="payment-api",
                problem_key="2" * 64,
                problem_type="PaymentHighErrorRate",
                scope_type="SERVICE",
                scope_key="3" * 64,
                scope_display_name="payment-api",
                signature_version="problem-signature.v1",
                environment="production",
                symptom="errors",
                title=alert.title,
                severity="high",
                representative_alert_id=alert.id,
                incident_id=incident_id,
                first_observed_at=NOW,
                last_observed_at=NOW,
                state_changed_at=NOW,
                last_member_at=NOW,
                active_count=0,
                total_count=1,
                impacted_resource_count=1,
                desired_correlation_version=1,
                reason_codes='["historical_group"]',
                explanation="历史告警组。",
                created_at=NOW,
                updated_at=NOW,
                version=1,
            )
        )
        members = table(
            "alert_group_members",
            *[
                column(name)
                for name in (
                    "alert_group_id",
                    "alert_id",
                    "alert_cycle",
                    "joined_alert_version",
                    "current_alert_version",
                    "current_state",
                    "current_severity",
                    "resource_type",
                    "resource_name",
                    "resource_key",
                    "reason_code",
                    "joined_at",
                    "updated_at",
                )
            ],
        )
        session.execute(
            insert(members).values(
                alert_group_id=group_id,
                alert_id=alert.id,
                alert_cycle=1,
                joined_alert_version=1,
                current_alert_version=1,
                current_state="RESOLVED",
                current_severity="high",
                resource_type="SERVICE",
                resource_name="payment-api",
                resource_key="4" * 64,
                reason_code="historical_group",
                joined_at=NOW,
                updated_at=NOW,
            )
        )
        session.commit()

    try:
        command.upgrade(alembic_config, "head")
        groups = table(
            "alert_groups",
            column("id"),
            column("state"),
            column("profile_version"),
            column("closed_at"),
            column("incident_id"),
        )
        members = table("alert_group_members", column("alert_group_id"))
        with Session(mysql_engine) as session:
            row = session.execute(select(groups).where(groups.c.id == group_id)).one()
            assert row.state == "CLOSED"
            assert row.profile_version == 1
            assert row.closed_at == NOW.replace(tzinfo=None)
            assert row.incident_id == incident_id
            assert (
                session.scalar(
                    select(members.c.alert_group_id).where(members.c.alert_group_id == group_id)
                )
                == group_id
            )
    finally:
        command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0009_alert_source_environment")

    inspector = inspect(mysql_engine)
    assert EVENT_TABLES.isdisjoint(inspector.get_table_names())
    assert {item["name"] for item in inspector.get_columns("alert_groups")}.isdisjoint(
        EVENT_COLUMNS
    )

    command.downgrade(alembic_config, "base")
