from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from incident_intelligence.domain.incidents import (
    Incident,
    IncidentActivity,
    IncidentActivityIds,
    IncidentAlertFact,
    IncidentStateConflict,
    acknowledge_incident,
    create_incident,
    link_alerts,
    resolve_incident,
)

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
INCIDENT_ID = "inc_11111111111111111111111111111111"
RULE_ID = "irl_22222222222222222222222222222222"
HIGH_ALERT_ID = "alt_33333333333333333333333333333333"
CRITICAL_ALERT_ID = "alt_44444444444444444444444444444444"
CREATED_ACTIVITY_ID = "iact_55555555555555555555555555555555"
LINKED_ACTIVITY_ID = "iact_66666666666666666666666666666666"
ESCALATED_ACTIVITY_ID = "iact_77777777777777777777777777777777"
RECOVERED_ACTIVITY_ID = "iact_88888888888888888888888888888888"
ACKNOWLEDGED_ACTIVITY_ID = "iact_99999999999999999999999999999999"
RESOLVED_ACTIVITY_ID = "iact_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _alert(
    alert_id: str = HIGH_ALERT_ID,
    *,
    alert_name: str = "HighErrorRate",
    state: str = "ACTIVE",
    severity: str = "high",
    received_at: datetime = NOW,
) -> IncidentAlertFact:
    return IncidentAlertFact.model_validate(
        {
            "id": alert_id,
            "alert_name": alert_name,
            "state": state,
            "severity": severity,
            "first_received_at": received_at,
        }
    )


def _incident(**overrides: object) -> Incident:
    values: dict[str, object] = {
        "id": INCIDENT_ID,
        "reference": "INC-20260901-001",
        "title": "production checkout异常",
        "state": "OPEN",
        "severity": "high",
        "environment": "production",
        "group_by": "SERVICE",
        "group_key": "checkout",
        "group_display_name": "checkout",
        "incident_rule_id": RULE_ID,
        "incident_rule_version": 3,
        "alert_count": 1,
        "active_alert_count": 1,
        "distinct_alert_name_count": 1,
        "version": 1,
        "opened_at": NOW,
        "acknowledged_at": None,
        "resolved_at": None,
        "last_alert_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return Incident.model_validate(values)


def _activity_ids() -> IncidentActivityIds:
    return IncidentActivityIds(
        alerts_linked=LINKED_ACTIVITY_ID,
        severity_escalated=ESCALATED_ACTIVITY_ID,
        all_alerts_recovered=RECOVERED_ACTIVITY_ID,
    )


def test_create_incident_builds_backend_owned_title_counts_and_activity() -> None:
    change = create_incident(
        incident_id=INCIDENT_ID,
        reference="INC-20260901-001",
        rule_id=RULE_ID,
        rule_version=3,
        environment="production",
        group_by="SERVICE",
        group_key="checkout",
        group_display_name="checkout",
        alerts=(
            _alert(),
            _alert(
                CRITICAL_ALERT_ID,
                alert_name="DatabaseUnavailable",
                severity="critical",
                received_at=NOW + timedelta(seconds=10),
            ),
        ),
        created_activity_id=CREATED_ACTIVITY_ID,
        now=NOW,
    )

    assert change.incident.title == "production checkout异常"
    assert change.incident.state == "OPEN"
    assert change.incident.severity == "critical"
    assert change.incident.alert_count == 2
    assert change.incident.active_alert_count == 2
    assert change.incident.distinct_alert_name_count == 2
    assert change.incident.last_alert_at == NOW + timedelta(seconds=10)
    assert change.new_alert_ids == (HIGH_ALERT_ID, CRITICAL_ALERT_ID)
    assert [(item.id, item.kind) for item in change.activities] == [
        (CREATED_ACTIVITY_ID, "INCIDENT_CREATED")
    ]
    assert change.changed is True


@pytest.mark.parametrize(
    "kind",
    [
        "EVIDENCE_COLLECTION_COMPLETED",
        "EVIDENCE_COLLECTION_PARTIAL",
        "EVIDENCE_COLLECTION_FAILED",
    ],
)
def test_incident_activity_accepts_deterministic_evidence_outcomes(kind: str) -> None:
    activity = IncidentActivity.model_validate(
        {
            "id": CREATED_ACTIVITY_ID,
            "incident_id": INCIDENT_ID,
            "kind": kind,
            "occurred_at": NOW,
            "actor_type": "SYSTEM",
            "actor": "evidence-collection",
            "summary": "监控取证已完成",
        }
    )

    assert activity.kind == kind


def test_create_incident_rejects_empty_alert_membership() -> None:
    with pytest.raises(ValueError, match="incident_alerts_required"):
        create_incident(
            incident_id=INCIDENT_ID,
            reference="INC-20260901-001",
            rule_id=RULE_ID,
            rule_version=3,
            environment="production",
            group_by="SERVICE",
            group_key="checkout",
            group_display_name="checkout",
            alerts=(),
            created_activity_id=CREATED_ACTIVITY_ID,
            now=NOW,
        )


def test_link_alerts_is_idempotent_and_only_escalates_severity() -> None:
    critical = _alert(
        CRITICAL_ALERT_ID,
        alert_name="DatabaseUnavailable",
        severity="critical",
        received_at=NOW + timedelta(minutes=1),
    )
    first = link_alerts(
        _incident(),
        existing_alert_ids=(HIGH_ALERT_ID,),
        alerts=(_alert(), critical),
        activity_ids=_activity_ids(),
        now=NOW + timedelta(minutes=1),
    )
    replay = link_alerts(
        first.incident,
        existing_alert_ids=(HIGH_ALERT_ID, CRITICAL_ALERT_ID),
        alerts=(_alert(), critical),
        activity_ids=_activity_ids(),
        now=NOW + timedelta(minutes=2),
    )

    assert first.incident.severity == "critical"
    assert first.incident.version == 2
    assert first.new_alert_ids == (CRITICAL_ALERT_ID,)
    assert [item.kind for item in first.activities] == [
        "ALERTS_LINKED",
        "SEVERITY_ESCALATED",
    ]
    assert replay.incident == first.incident
    assert replay.activities == ()
    assert replay.new_alert_ids == ()
    assert replay.changed is False


def test_link_alerts_rejects_incomplete_current_membership() -> None:
    with pytest.raises(ValueError, match="incident_alert_membership_incomplete"):
        link_alerts(
            _incident(),
            existing_alert_ids=(HIGH_ALERT_ID, CRITICAL_ALERT_ID),
            alerts=(_alert(),),
            activity_ids=_activity_ids(),
            now=NOW,
        )


def test_all_alerts_recovered_records_fact_once_without_resolving_incident() -> None:
    recovered = _alert(state="RESOLVED")
    first = link_alerts(
        _incident(),
        existing_alert_ids=(HIGH_ALERT_ID,),
        alerts=(recovered,),
        activity_ids=_activity_ids(),
        now=NOW + timedelta(minutes=1),
    )
    replay = link_alerts(
        first.incident,
        existing_alert_ids=(HIGH_ALERT_ID,),
        alerts=(recovered,),
        activity_ids=_activity_ids(),
        now=NOW + timedelta(minutes=2),
    )

    assert first.incident.state == "OPEN"
    assert first.incident.active_alert_count == 0
    assert [item.kind for item in first.activities] == ["ALL_ALERTS_RECOVERED"]
    assert replay.changed is False
    assert replay.activities == ()


def test_acknowledge_and_resolve_follow_explicit_state_machine() -> None:
    acknowledged = acknowledge_incident(
        _incident(),
        activity_id=ACKNOWLEDGED_ACTIVITY_ID,
        actor="manual-api-client",
        now=NOW + timedelta(minutes=1),
    )
    resolved = resolve_incident(
        acknowledged.incident,
        activity_id=RESOLVED_ACTIVITY_ID,
        resolution_summary="数据库连接池参数已经恢复",
        actor="manual-api-client",
        now=NOW + timedelta(minutes=2),
    )

    assert acknowledged.incident.state == "ACKNOWLEDGED"
    assert acknowledged.incident.acknowledged_at == NOW + timedelta(minutes=1)
    assert acknowledged.activities[0].kind == "ACKNOWLEDGED"
    assert resolved.incident.state == "RESOLVED"
    assert resolved.incident.resolved_at == NOW + timedelta(minutes=2)
    assert resolved.incident.version == 3
    assert resolved.activities[0].summary == "数据库连接池参数已经恢复"


def test_open_incident_can_resolve_directly() -> None:
    resolved = resolve_incident(
        _incident(),
        activity_id=RESOLVED_ACTIVITY_ID,
        resolution_summary="告警确认后服务已经自行恢复",
        actor="manual-api-client",
        now=NOW + timedelta(minutes=1),
    )

    assert resolved.incident.state == "RESOLVED"
    assert resolved.incident.acknowledged_at is None


def test_resolution_requires_bounded_summary_and_resolved_is_terminal() -> None:
    with pytest.raises(IncidentStateConflict, match="resolution_summary_required"):
        resolve_incident(
            _incident(),
            activity_id=RESOLVED_ACTIVITY_ID,
            resolution_summary="  ",
            actor="manual-api-client",
            now=NOW,
        )
    with pytest.raises(IncidentStateConflict, match="resolution_summary_too_long"):
        resolve_incident(
            _incident(),
            activity_id=RESOLVED_ACTIVITY_ID,
            resolution_summary="字" * 2_001,
            actor="manual-api-client",
            now=NOW,
        )

    resolved = resolve_incident(
        _incident(),
        activity_id=RESOLVED_ACTIVITY_ID,
        resolution_summary="服务已恢复",
        actor="manual-api-client",
        now=NOW,
    )
    with pytest.raises(IncidentStateConflict, match="incident_already_resolved"):
        acknowledge_incident(
            resolved.incident,
            activity_id=ACKNOWLEDGED_ACTIVITY_ID,
            actor="manual-api-client",
            now=NOW,
        )
    with pytest.raises(IncidentStateConflict, match="incident_already_resolved"):
        resolve_incident(
            resolved.incident,
            activity_id=RESOLVED_ACTIVITY_ID,
            resolution_summary="重复解决",
            actor="manual-api-client",
            now=NOW,
        )


def test_incident_models_reject_unknown_fields_and_invalid_activity_ids() -> None:
    with pytest.raises(ValidationError):
        Incident.model_validate({**_incident().model_dump(), "scenario_id": "forbidden"})
    with pytest.raises(ValidationError):
        IncidentActivityIds(
            alerts_linked="not-an-activity-id",
            severity_escalated=ESCALATED_ACTIVITY_ID,
            all_alerts_recovered=RECOVERED_ACTIVITY_ID,
        )


@pytest.mark.parametrize(
    "metadata",
    [
        {"k" * 65: "value"},
        {"detail": "字" * 513},
        {f"key-{index}": index for index in range(21)},
    ],
)
def test_incident_activity_rejects_oversized_metadata(
    metadata: dict[str, str | int],
) -> None:
    with pytest.raises(ValidationError):
        IncidentActivity(
            id=CREATED_ACTIVITY_ID,
            incident_id=INCIDENT_ID,
            kind="INCIDENT_CREATED",
            occurred_at=NOW,
            actor_type="SYSTEM",
            actor="incident-evaluation",
            summary="Incident 已创建",
            metadata=metadata,
        )
