# ruff: noqa: RUF001

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from incident_intelligence.domain.alerts import AlertState
from incident_intelligence.domain.incident_rules import RuleGroupBy
from incident_intelligence.domain.models import (
    AlertName,
    Environment,
    Severity,
    UtcAwareDatetime,
)

IncidentState = Literal["OPEN", "ACKNOWLEDGED", "RESOLVED"]
IncidentActivityKind = Literal[
    "INCIDENT_CREATED",
    "ALERTS_LINKED",
    "SEVERITY_ESCALATED",
    "ALL_ALERTS_RECOVERED",
    "ACKNOWLEDGED",
    "RESOLVED",
    "FEISHU_MESSAGE_RECORDED",
    "NOTIFICATION_FAILED",
    "EVIDENCE_COLLECTION_COMPLETED",
    "EVIDENCE_COLLECTION_PARTIAL",
    "EVIDENCE_COLLECTION_FAILED",
]
IncidentActorType = Literal["SYSTEM", "USER", "FEISHU"]
ActivityMetadataValue = str | int | bool

IncidentReference = Annotated[
    str,
    StringConstraints(pattern=r"^INC-[0-9]{8}-[0-9]{3,9}$"),
]
IncidentGroupKey = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=257),
]
IncidentGroupDisplayName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=257),
]
IncidentActor = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
IncidentResolutionSummary = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
]

SEVERITY_RANK: dict[Severity, int] = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


class _FrozenIncidentModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class IncidentAlertFact(_FrozenIncidentModel):
    id: str = Field(pattern=r"^alt_[0-9a-f]{32}$")
    alert_name: AlertName
    state: AlertState
    severity: Severity
    first_received_at: UtcAwareDatetime


class Incident(_FrozenIncidentModel):
    id: str = Field(pattern=r"^inc_[0-9a-f]{32}$")
    reference: IncidentReference
    title: str = Field(min_length=1, max_length=320)
    state: IncidentState
    severity: Severity
    environment: Environment
    group_by: RuleGroupBy
    group_key: IncidentGroupKey
    group_display_name: IncidentGroupDisplayName
    incident_rule_id: str = Field(pattern=r"^irl_[0-9a-f]{32}$")
    incident_rule_version: int = Field(ge=1)
    alert_count: int = Field(ge=1)
    active_alert_count: int = Field(ge=0)
    distinct_alert_name_count: int = Field(ge=1)
    version: int = Field(ge=1)
    opened_at: UtcAwareDatetime
    acknowledged_at: UtcAwareDatetime | None = None
    resolved_at: UtcAwareDatetime | None = None
    resolution_summary: IncidentResolutionSummary | None = None
    last_alert_at: UtcAwareDatetime
    created_at: UtcAwareDatetime
    updated_at: UtcAwareDatetime

    @model_validator(mode="after")
    def validate_state(self) -> Incident:
        if self.active_alert_count > self.alert_count:
            raise ValueError("active_alert_count 不得大于 alert_count")
        if self.state == "OPEN" and (
            self.acknowledged_at is not None
            or self.resolved_at is not None
            or self.resolution_summary is not None
        ):
            raise ValueError("OPEN Incident 不能有确认或解决事实")
        if self.state == "ACKNOWLEDGED" and (
            self.acknowledged_at is None
            or self.resolved_at is not None
            or self.resolution_summary is not None
        ):
            raise ValueError("ACKNOWLEDGED Incident 状态事实不完整")
        if self.state == "RESOLVED" and (
            self.resolved_at is None or self.resolution_summary is None
        ):
            raise ValueError("RESOLVED Incident 必须有解决时间和说明")
        return self


class IncidentAlertLink(_FrozenIncidentModel):
    incident_id: str = Field(pattern=r"^inc_[0-9a-f]{32}$")
    alert_id: str = Field(pattern=r"^alt_[0-9a-f]{32}$")
    incident_rule_version: int = Field(ge=1)
    first_trigger_window: bool
    linked_at: UtcAwareDatetime


class IncidentActivity(_FrozenIncidentModel):
    id: str = Field(pattern=r"^iact_[0-9a-f]{32}$")
    incident_id: str = Field(pattern=r"^inc_[0-9a-f]{32}$")
    kind: IncidentActivityKind
    occurred_at: UtcAwareDatetime
    actor_type: IncidentActorType
    actor: IncidentActor
    summary: str = Field(min_length=1, max_length=4_000)
    metadata: dict[str, ActivityMetadataValue] = Field(default_factory=dict, max_length=20)

    @model_validator(mode="after")
    def validate_metadata(self) -> IncidentActivity:
        for key, value in self.metadata.items():
            if not 1 <= len(key) <= 64:
                raise ValueError("activity metadata key 长度必须为 1 到 64")
            if isinstance(value, str) and len(value) > 512:
                raise ValueError("activity metadata string 长度不能超过 512")
        return self


class IncidentActivityIds(_FrozenIncidentModel):
    alerts_linked: str = Field(pattern=r"^iact_[0-9a-f]{32}$")
    severity_escalated: str = Field(pattern=r"^iact_[0-9a-f]{32}$")
    all_alerts_recovered: str = Field(pattern=r"^iact_[0-9a-f]{32}$")


class IncidentChange(_FrozenIncidentModel):
    incident: Incident
    activities: tuple[IncidentActivity, ...]
    new_alert_ids: tuple[str, ...]
    changed: bool


class IncidentStateConflict(ValueError):
    pass


def create_incident(
    *,
    incident_id: str,
    reference: str,
    rule_id: str,
    rule_version: int,
    environment: Environment,
    group_by: RuleGroupBy,
    group_key: str,
    group_display_name: str,
    alerts: tuple[IncidentAlertFact, ...],
    created_activity_id: str,
    now: UtcAwareDatetime,
) -> IncidentChange:
    members = _unique_alerts(alerts)
    if not members:
        raise ValueError("incident_alerts_required")
    severity = _highest_severity(members)
    incident = Incident(
        id=incident_id,
        reference=reference,
        title=f"{environment} {group_display_name.strip()}异常",
        state="OPEN",
        severity=severity,
        environment=environment,
        group_by=group_by,
        group_key=group_key,
        group_display_name=group_display_name,
        incident_rule_id=rule_id,
        incident_rule_version=rule_version,
        alert_count=len(members),
        active_alert_count=_active_count(members),
        distinct_alert_name_count=_distinct_name_count(members),
        version=1,
        opened_at=now,
        last_alert_at=max(item.first_received_at for item in members),
        created_at=now,
        updated_at=now,
    )
    activity = _activity(
        activity_id=created_activity_id,
        incident=incident,
        kind="INCIDENT_CREATED",
        actor_type="SYSTEM",
        actor="incident-evaluation",
        summary=f"{reference} 已由规则自动创建",
        metadata={
            "incident_rule_id": rule_id,
            "incident_rule_version": rule_version,
            "initial_alert_count": len(members),
        },
        now=now,
    )
    return IncidentChange(
        incident=incident,
        activities=(activity,),
        new_alert_ids=tuple(item.id for item in members),
        changed=True,
    )


def link_alerts(
    incident: Incident,
    *,
    existing_alert_ids: tuple[str, ...],
    alerts: tuple[IncidentAlertFact, ...],
    activity_ids: IncidentActivityIds,
    now: UtcAwareDatetime,
) -> IncidentChange:
    members = _unique_alerts(alerts)
    member_ids = {item.id for item in members}
    existing_ids = set(existing_alert_ids)
    if not existing_ids.issubset(member_ids):
        raise ValueError("incident_alert_membership_incomplete")
    new_alert_ids = tuple(item.id for item in members if item.id not in existing_ids)
    severity = max(
        (incident.severity, _highest_severity(members)),
        key=SEVERITY_RANK.__getitem__,
    )
    alert_count = len(members)
    active_alert_count = _active_count(members)
    distinct_alert_name_count = _distinct_name_count(members)
    last_alert_at = max(
        incident.last_alert_at,
        *(item.first_received_at for item in members),
    )
    changed = (
        bool(new_alert_ids)
        or severity != incident.severity
        or alert_count != incident.alert_count
        or active_alert_count != incident.active_alert_count
        or distinct_alert_name_count != incident.distinct_alert_name_count
        or last_alert_at != incident.last_alert_at
    )
    if not changed:
        return IncidentChange(
            incident=incident,
            activities=(),
            new_alert_ids=(),
            changed=False,
        )

    updated = Incident.model_validate(
        incident.model_copy(
            update={
                "severity": severity,
                "alert_count": alert_count,
                "active_alert_count": active_alert_count,
                "distinct_alert_name_count": distinct_alert_name_count,
                "last_alert_at": last_alert_at,
                "version": incident.version + 1,
                "updated_at": now,
            }
        )
    )
    activities: list[IncidentActivity] = []
    if new_alert_ids:
        activities.append(
            _activity(
                activity_id=activity_ids.alerts_linked,
                incident=updated,
                kind="ALERTS_LINKED",
                actor_type="SYSTEM",
                actor="incident-evaluation",
                summary=f"新增关联 {len(new_alert_ids)} 条告警",
                metadata={"added_alert_count": len(new_alert_ids)},
                now=now,
            )
        )
    if severity != incident.severity:
        activities.append(
            _activity(
                activity_id=activity_ids.severity_escalated,
                incident=updated,
                kind="SEVERITY_ESCALATED",
                actor_type="SYSTEM",
                actor="incident-evaluation",
                summary=f"严重级别由 {incident.severity} 升级为 {severity}",
                metadata={"from": incident.severity, "to": severity},
                now=now,
            )
        )
    if incident.active_alert_count > 0 and active_alert_count == 0:
        activities.append(
            _activity(
                activity_id=activity_ids.all_alerts_recovered,
                incident=updated,
                kind="ALL_ALERTS_RECOVERED",
                actor_type="SYSTEM",
                actor="incident-evaluation",
                summary="关联告警已全部恢复，Incident 仍等待人工解决",
                metadata={"incident_state": incident.state},
                now=now,
            )
        )
    return IncidentChange(
        incident=updated,
        activities=tuple(activities),
        new_alert_ids=new_alert_ids,
        changed=True,
    )


def acknowledge_incident(
    incident: Incident,
    *,
    activity_id: str,
    actor: str,
    now: UtcAwareDatetime,
) -> IncidentChange:
    if incident.state == "RESOLVED":
        raise IncidentStateConflict("incident_already_resolved")
    if incident.state == "ACKNOWLEDGED":
        raise IncidentStateConflict("incident_already_acknowledged")
    updated = Incident.model_validate(
        incident.model_copy(
            update={
                "state": "ACKNOWLEDGED",
                "acknowledged_at": now,
                "version": incident.version + 1,
                "updated_at": now,
            }
        )
    )
    activity = _activity(
        activity_id=activity_id,
        incident=updated,
        kind="ACKNOWLEDGED",
        actor_type="USER",
        actor=actor,
        summary="Incident 已确认，进入处理中",
        metadata={},
        now=now,
    )
    return IncidentChange(
        incident=updated,
        activities=(activity,),
        new_alert_ids=(),
        changed=True,
    )


def resolve_incident(
    incident: Incident,
    *,
    activity_id: str,
    resolution_summary: str,
    actor: str,
    now: UtcAwareDatetime,
) -> IncidentChange:
    if incident.state == "RESOLVED":
        raise IncidentStateConflict("incident_already_resolved")
    normalized_summary = resolution_summary.strip()
    if not normalized_summary:
        raise IncidentStateConflict("resolution_summary_required")
    if len(normalized_summary) > 2_000:
        raise IncidentStateConflict("resolution_summary_too_long")
    updated = Incident.model_validate(
        incident.model_copy(
            update={
                "state": "RESOLVED",
                "resolved_at": now,
                "resolution_summary": normalized_summary,
                "version": incident.version + 1,
                "updated_at": now,
            }
        )
    )
    activity = _activity(
        activity_id=activity_id,
        incident=updated,
        kind="RESOLVED",
        actor_type="USER",
        actor=actor,
        summary=normalized_summary[:500],
        metadata={},
        now=now,
    )
    return IncidentChange(
        incident=updated,
        activities=(activity,),
        new_alert_ids=(),
        changed=True,
    )


def _unique_alerts(alerts: tuple[IncidentAlertFact, ...]) -> tuple[IncidentAlertFact, ...]:
    unique: dict[str, IncidentAlertFact] = {}
    for alert in alerts:
        previous = unique.get(alert.id)
        if previous is not None and previous != alert:
            raise ValueError("incident_alert_fact_conflict")
        unique[alert.id] = alert
    return tuple(unique.values())


def _highest_severity(alerts: tuple[IncidentAlertFact, ...]) -> Severity:
    if not alerts:
        raise ValueError("incident_alerts_required")
    return max((item.severity for item in alerts), key=SEVERITY_RANK.__getitem__)


def _active_count(alerts: tuple[IncidentAlertFact, ...]) -> int:
    return sum(item.state == "ACTIVE" for item in alerts)


def _distinct_name_count(alerts: tuple[IncidentAlertFact, ...]) -> int:
    return len({item.alert_name for item in alerts})


def _activity(
    *,
    activity_id: str,
    incident: Incident,
    kind: IncidentActivityKind,
    actor_type: IncidentActorType,
    actor: str,
    summary: str,
    metadata: dict[str, ActivityMetadataValue],
    now: UtcAwareDatetime,
) -> IncidentActivity:
    return IncidentActivity(
        id=activity_id,
        incident_id=incident.id,
        kind=kind,
        occurred_at=now,
        actor_type=actor_type,
        actor=actor,
        summary=summary,
        metadata=metadata,
    )
