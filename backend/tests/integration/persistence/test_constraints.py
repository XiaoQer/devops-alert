from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from sqlalchemy.orm import Session

from incident_intelligence.domain.alert_sources import (
    ALERTMANAGER_COMPAT_SOURCE_ID,
    MANUAL_SYSTEM_SOURCE_ID,
)
from incident_intelligence.ids import new_id
from incident_intelligence.persistence.models import (
    AlertRow,
    CorrelationDecisionRow,
    CorrelationJobRow,
    IncidentActivityRow,
    IncidentAlertLinkRow,
    IncidentOperationRow,
    IncidentRow,
    ServiceCatalogEntryRow,
    ServiceDependencyRow,
    SignalEventRow,
)

NOW = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)


def make_signal(**overrides: object) -> SignalEventRow:
    values: dict[str, object] = {
        "id": new_id("sig"),
        "alert_source_id": MANUAL_SYSTEM_SOURCE_ID,
        "source": "manual",
        "source_event_id": "manual-001",
        "event_type": "manual.reported",
        "title": "支付接口错误率升高",
        "summary": "支付接口在生产环境持续返回错误",
        "severity": "high",
        "service": "payment-api",
        "environment": "production",
        "observed_at": NOW,
        "received_at": NOW,
        "facts": {"region": "cn-east-1"},
        "payload_fingerprint": "a" * 64,
        "created_at": NOW,
        "version": 1,
    }
    values.update(overrides)
    return SignalEventRow(**values)


def make_alert(signal_event_id: str, **overrides: object) -> AlertRow:
    values: dict[str, object] = {
        "id": new_id("alt"),
        "signal_event_id": signal_event_id,
        "alert_source_id": ALERTMANAGER_COMPAT_SOURCE_ID,
        "source": "alertmanager",
        "source_instance": "a" * 64,
        "source_alert_key": "payment-high-error-rate",
        "state": "ACTIVE",
        "title": "支付接口错误率升高",
        "severity": "high",
        "service": "payment-api",
        "environment": "production",
        "first_observed_at": NOW,
        "last_observed_at": NOW,
        "state_changed_at": NOW,
        "created_at": NOW,
        "version": 1,
    }
    values.update(overrides)
    return AlertRow(**values)


def make_service(**overrides: object) -> ServiceCatalogEntryRow:
    values: dict[str, object] = {
        "id": new_id("svc"),
        "service": "payment-api",
        "environment": "production",
        "owner_team": "payments",
        "state": "ACTIVE",
        "created_at": NOW,
        "updated_at": NOW,
        "version": 1,
    }
    values.update(overrides)
    return ServiceCatalogEntryRow(**values)


def seed_alert(session: Session) -> AlertRow:
    signal = make_signal(source_event_id=new_id("sig"))
    session.add(signal)
    session.flush()
    alert = make_alert(
        signal.id,
        source_alert_key=new_id("alt"),
    )
    session.add(alert)
    session.flush()
    return alert


def seed_incident(session: Session, **overrides: object) -> IncidentRow:
    alert = seed_alert(session)
    values: dict[str, object] = {
        "id": new_id("inc"),
        "primary_alert_id": alert.id,
        "state": "DETECTED",
        "title": alert.title,
        "severity": alert.severity,
        "service": alert.service,
        "environment": alert.environment,
        "detected_at": NOW,
        "assignee": None,
        "claimed_at": None,
        "state_changed_at": NOW,
        "resolved_at": None,
        "closed_at": None,
        "created_at": NOW,
        "version": 1,
    }
    values.update(overrides)
    incident = IncidentRow(**values)
    session.add(incident)
    session.flush()
    return incident


def make_activity(incident_id: str, **overrides: object) -> IncidentActivityRow:
    values: dict[str, object] = {
        "id": new_id("iact"),
        "incident_id": incident_id,
        "kind": "NOTE_ADDED",
        "actor": "manual-api-client",
        "from_state": None,
        "to_state": None,
        "note_category": "GENERAL",
        "message": "已开始人工排查",
        "resolution_category": None,
        "resolution_actions": None,
        "root_cause": None,
        "incident_version": 2,
        "created_at": NOW,
    }
    values.update(overrides)
    return IncidentActivityRow(**values)


def make_operation(incident_id: str, activity_id: str, **overrides: object) -> IncidentOperationRow:
    values: dict[str, object] = {
        "id": new_id("iop"),
        "scope": "incident.note",
        "idempotency_key_hash": "a" * 64,
        "command_fingerprint": "b" * 64,
        "incident_id": incident_id,
        "action": "ADD_NOTE",
        "result_state": "DETECTED",
        "result_assignee": None,
        "result_version": 2,
        "activity_id": activity_id,
        "completed_at": NOW,
    }
    values.update(overrides)
    return IncidentOperationRow(**values)


def make_job(alert_id: str, **overrides: object) -> CorrelationJobRow:
    values: dict[str, object] = {
        "id": new_id("cjob"),
        "alert_source_id": ALERTMANAGER_COMPAT_SOURCE_ID,
        "alert_id": alert_id,
        "alert_version": 1,
        "state": "PENDING",
        "attempts": 0,
        "available_at": NOW,
        "lease_owner": None,
        "lease_expires_at": None,
        "last_error_code": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return CorrelationJobRow(**values)


def test_incident_assignment_fields_must_be_both_null_or_both_present(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session:
        alert = seed_alert(session)
        session.add(
            IncidentRow(
                id=new_id("inc"),
                primary_alert_id=alert.id,
                state="DETECTED",
                title=alert.title,
                severity=alert.severity,
                service=alert.service,
                environment=alert.environment,
                detected_at=NOW,
                assignee="manual-api-client",
                claimed_at=None,
                state_changed_at=NOW,
                resolved_at=None,
                closed_at=None,
                created_at=NOW,
                version=1,
            )
        )
        with pytest.raises(OperationalError):
            session.commit()


@pytest.mark.parametrize(
    ("state", "resolved_at", "closed_at"),
    [
        ("DETECTED", NOW, None),
        ("RESOLVED", None, None),
        ("RESOLVED", NOW, NOW),
        ("CLOSED", None, NOW),
        ("CLOSED", NOW, None),
    ],
)
def test_incident_state_requires_matching_resolution_times(
    migrated_engine: Engine,
    state: str,
    resolved_at: datetime | None,
    closed_at: datetime | None,
) -> None:
    with Session(migrated_engine) as session, pytest.raises(OperationalError):
        seed_incident(
            session,
            state=state,
            resolved_at=resolved_at,
            closed_at=closed_at,
        )
        session.commit()


def test_activity_values_and_version_are_bounded(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        incident = seed_incident(session)
        session.add(make_activity(incident.id, kind="ARBITRARY", incident_version=0))
        with pytest.raises(OperationalError):
            session.commit()


def test_operation_hash_and_fingerprint_are_fixed_length(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        incident = seed_incident(session)
        activity = make_activity(incident.id)
        session.add(activity)
        session.flush()
        session.add(make_operation(incident.id, activity.id, idempotency_key_hash="short"))
        with pytest.raises(OperationalError):
            session.commit()


def test_operation_scope_and_key_are_unique(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        incident = seed_incident(session)
        first_activity = make_activity(incident.id)
        second_activity = make_activity(incident.id, id=new_id("iact"), incident_version=3)
        session.add_all([first_activity, second_activity])
        session.flush()
        session.add(make_operation(incident.id, first_activity.id))
        session.commit()
        session.add(
            make_operation(
                incident.id,
                second_activity.id,
                id=new_id("iop"),
                command_fingerprint="c" * 64,
                result_version=3,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_source_identity_is_unique(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(make_signal())
        session.commit()
        session.add(make_signal(id=new_id("sig")))

        with pytest.raises(IntegrityError):
            session.commit()

        session.rollback()
        assert session.scalar(select(func.count()).select_from(SignalEventRow)) == 1


def test_alert_rejects_incident_state_value(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        signal = make_signal()
        session.add(signal)
        session.flush()
        session.add(
            AlertRow(
                id=new_id("alt"),
                signal_event_id=signal.id,
                alert_source_id=MANUAL_SYSTEM_SOURCE_ID,
                source="manual",
                source_instance="a" * 64,
                source_alert_key="b" * 64,
                state="DETECTED",
                title=signal.title,
                severity=signal.severity,
                service=signal.service,
                environment=signal.environment,
                first_observed_at=NOW,
                last_observed_at=NOW,
                state_changed_at=NOW,
                created_at=NOW,
                version=1,
            )
        )

        with pytest.raises(OperationalError):
            session.commit()

        session.rollback()


def test_alert_requires_existing_signal(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(
            AlertRow(
                id=new_id("alt"),
                signal_event_id=new_id("sig"),
                alert_source_id=MANUAL_SYSTEM_SOURCE_ID,
                source="manual",
                source_instance="a" * 64,
                source_alert_key="b" * 64,
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

        with pytest.raises(IntegrityError):
            session.commit()

        session.rollback()


def test_signal_title_length_is_enforced_by_mysql(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(make_signal(title="x" * 201))

        with pytest.raises(DataError):
            session.commit()

        session.rollback()


def test_exact_alert_identity_is_unique(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        first_signal = make_signal(source_event_id="event-1")
        second_signal = make_signal(id=new_id("sig"), source_event_id="event-2")
        session.add_all([first_signal, second_signal])
        session.flush()
        session.add_all(
            [
                make_alert(first_signal.id),
                make_alert(second_signal.id, id=new_id("alt")),
            ]
        )

        with pytest.raises(IntegrityError):
            session.commit()

        session.rollback()
        assert session.scalar(select(func.count()).select_from(AlertRow)) == 0


def test_catalog_identity_and_dependency_edge_are_unique(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        payment = make_service()
        inventory = make_service(service="inventory-api")
        session.add_all([payment, inventory])
        session.commit()

        session.add(make_service(id=new_id("svc")))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add_all(
            [
                ServiceDependencyRow(
                    id=new_id("dep"),
                    caller_service_id=payment.id,
                    dependency_service_id=inventory.id,
                    state="ACTIVE",
                    created_at=NOW,
                    updated_at=NOW,
                    version=1,
                ),
                ServiceDependencyRow(
                    id=new_id("dep"),
                    caller_service_id=payment.id,
                    dependency_service_id=inventory.id,
                    state="ACTIVE",
                    created_at=NOW,
                    updated_at=NOW,
                    version=1,
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize(
    "overrides",
    [
        {"environment": "private"},
        {"service": ""},
        {"owner_team": ""},
    ],
)
def test_catalog_rejects_invalid_environment_and_empty_identity_fields(
    migrated_engine: Engine,
    overrides: dict[str, object],
) -> None:
    with Session(migrated_engine) as session:
        session.add(make_service(**overrides))
        with pytest.raises(OperationalError):
            session.commit()


def test_dependency_rejects_self_edge_and_invalid_state(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        payment = make_service()
        session.add(payment)
        session.commit()

        session.add(
            ServiceDependencyRow(
                id=new_id("dep"),
                caller_service_id=payment.id,
                dependency_service_id=payment.id,
                state="ACTIVE",
                created_at=NOW,
                updated_at=NOW,
                version=1,
            )
        )
        with pytest.raises(OperationalError):
            session.commit()
        session.rollback()

        inventory = make_service(service="inventory-api")
        session.add(inventory)
        session.commit()
        session.add(
            ServiceDependencyRow(
                id=new_id("dep"),
                caller_service_id=payment.id,
                dependency_service_id=inventory.id,
                state="DELETED",
                created_at=NOW,
                updated_at=NOW,
                version=1,
            )
        )
        with pytest.raises(OperationalError):
            session.commit()


def test_job_rejects_duplicate_alert_version_and_more_than_five_attempts(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session:
        alert = seed_alert(session)
        session.add(make_job(alert.id))
        session.commit()

        session.add(make_job(alert.id))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(make_job(alert.id, alert_version=2, attempts=6))
        with pytest.raises(OperationalError):
            session.commit()


def test_alert_can_only_be_linked_to_one_incident(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        alert = seed_alert(session)
        first_incident = IncidentRow(
            id=new_id("inc"),
            primary_alert_id=alert.id,
            state="DETECTED",
            title=alert.title,
            severity=alert.severity,
            service=alert.service,
            environment=alert.environment,
            detected_at=NOW,
            state_changed_at=NOW,
            resolved_at=None,
            closed_at=None,
            created_at=NOW,
            version=1,
        )
        second_incident = IncidentRow(
            id=new_id("inc"),
            primary_alert_id=alert.id,
            state="DETECTED",
            title=alert.title,
            severity=alert.severity,
            service=alert.service,
            environment=alert.environment,
            detected_at=NOW,
            state_changed_at=NOW,
            resolved_at=None,
            closed_at=None,
            created_at=NOW,
            version=1,
        )
        session.add_all([first_incident, second_incident])
        session.flush()
        session.add_all(
            [
                IncidentAlertLinkRow(
                    incident_id=first_incident.id,
                    alert_id=alert.id,
                    relation="PRIMARY",
                    decision_id=None,
                    linked_at=NOW,
                    created_at=NOW,
                ),
                IncidentAlertLinkRow(
                    incident_id=second_incident.id,
                    alert_id=alert.id,
                    relation="RELATED",
                    decision_id=None,
                    linked_at=NOW,
                    created_at=NOW,
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize(
    ("reason_codes", "candidate_incident_ids"),
    [
        ({"not": "an-array"}, []),
        (["no_same_service_candidate"], {"not": "an-array"}),
        (["no_same_service_candidate"], [f"inc_{index:032x}" for index in range(21)]),
    ],
)
def test_decision_json_collections_are_arrays_and_bounded(
    migrated_engine: Engine,
    reason_codes: object,
    candidate_incident_ids: object,
) -> None:
    with Session(migrated_engine) as session:
        alert = seed_alert(session)
        job = make_job(alert.id)
        session.add(job)
        session.flush()
        session.add(
            CorrelationDecisionRow(
                id=new_id("cdec"),
                job_id=job.id,
                alert_id=alert.id,
                alert_version=1,
                incident_id=None,
                outcome="CREATED_NO_MATCH",
                rule_version="correlation.v1",
                reason_codes=reason_codes,
                facts={"service": "payment-api"},
                candidate_incident_ids=candidate_incident_ids,
                explanation="窗口内没有同服务事故，已创建独立事故。",  # noqa: RUF001
                created_at=NOW,
            )
        )
        with pytest.raises(OperationalError):
            session.commit()
