from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError

from incident_intelligence.domain.incidents import (
    Incident,
    IncidentActivity,
    IncidentChange,
    IncidentStateConflict,
    acknowledge_incident,
    resolve_incident,
)
from incident_intelligence.domain.models import UtcAwareDatetime
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.alert_center_repository import (
    AlertRecord,
    AlertRepository,
)
from incident_intelligence.persistence.evidence_repository import EvidenceRunRepository
from incident_intelligence.persistence.incident_repository import (
    IncidentFeishuThreadRepository,
    IncidentNotificationRecord,
    IncidentNotificationRepository,
    IncidentNotificationRouteRepository,
    IncidentOperationRecord,
    IncidentOperationRepository,
    IncidentRepository,
)
from incident_intelligence.persistence.incident_rule_repository import IncidentRuleRepository
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork

IncidentAction = Literal["ACKNOWLEDGE", "RESOLVE"]


class IncidentListItem(Incident):
    rule_name: str
    rule_summary: str


class IncidentPageView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[IncidentListItem, ...]
    total: int
    limit: int
    offset: int


class IncidentAlertView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    alert_name: str
    summary: str
    description: str
    state: str
    severity: str
    environment: str
    service: str | None
    entity_type: str
    entity_display_name: str
    source_id: str
    source_name: str
    first_observed_at: UtcAwareDatetime
    last_observed_at: UtcAwareDatetime
    first_received_at: UtcAwareDatetime
    last_received_at: UtcAwareDatetime
    resolved_at: UtcAwareDatetime | None


class FeishuCollaborationView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    configured: bool
    route_name: str | None
    chat_id_masked: str | None
    thread_bound: bool
    last_synced_at: UtcAwareDatetime | None
    notification_state: str | None
    last_error_code: str | None


class LatestEvidenceRunView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    state: str
    succeeded_count: int
    missing_count: int
    failed_count: int
    completed_at: UtcAwareDatetime | None


class IncidentDetailView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    incident: Incident
    rule_name: str
    rule_summary: str
    alerts: tuple[IncidentAlertView, ...]
    alerts_truncated: bool
    activities: tuple[IncidentActivity, ...]
    activities_truncated: bool
    feishu: FeishuCollaborationView
    latest_evidence_run: LatestEvidenceRunView | None


class IncidentMutationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    incident: Incident
    replayed: bool


@dataclass(frozen=True, slots=True)
class IncidentNotFound(Exception):
    reason_code: str = "incident_not_found"


@dataclass(frozen=True, slots=True)
class IncidentVersionConflict(Exception):
    reason_code: str = "incident_version_conflict"


@dataclass(frozen=True, slots=True)
class IncidentIdempotencyConflict(Exception):
    reason_code: str = "idempotency_conflict"


class IncidentService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[IdPrefix], str] = new_id,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory

    def list(
        self,
        *,
        states: tuple[str, ...] = ("OPEN", "ACKNOWLEDGED"),
        environment: str | None = None,
        severity: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> IncidentPageView:
        if not 1 <= limit <= 100 or not 0 <= offset <= 10_000:
            raise ValueError("invalid_incident_page")
        with self._uow_factory() as uow:
            page = _incidents(uow).list(
                states=states,
                environment=environment,
                severity=severity,
                search=search,
                limit=limit,
                offset=offset,
            )
            rules = _rules(uow)
            rule_cache = {
                rule_id: rules.get(rule_id)
                for rule_id in {incident.incident_rule_id for incident in page.items}
            }
            items: list[IncidentListItem] = []
            for incident in page.items:
                rule = rule_cache[incident.incident_rule_id]
                if rule is None:
                    continue
                items.append(
                    IncidentListItem.model_validate(
                        {
                            **incident.model_dump(mode="python"),
                            "rule_name": rule.name,
                            "rule_summary": rule.summary,
                        }
                    )
                )
            return IncidentPageView(
                items=tuple(items),
                total=page.total,
                limit=page.limit,
                offset=page.offset,
            )

    def get(self, incident_id: str) -> IncidentDetailView:
        with self._uow_factory() as uow:
            incident = _incidents(uow).get(incident_id)
            if incident is None:
                raise IncidentNotFound()
            rule = _rules(uow).get(incident.incident_rule_id)
            if rule is None:
                raise IncidentNotFound()
            alert_ids = _incidents(uow).list_alert_ids(incident_id)
            alert_records = _alerts(uow).list_alerts_by_ids(alert_ids, limit=501)
            activity_records = _incidents(uow).list_activities(incident_id, limit=1001)
            route = _routes(uow).find_enabled(incident.environment)
            thread = _threads(uow).get_by_incident(incident_id)
            notification = _notifications(uow).latest_for_incident(incident_id)
            evidence_run = _evidence_runs(uow).latest_for_incident(incident_id)
            return IncidentDetailView(
                incident=incident,
                rule_name=rule.name,
                rule_summary=rule.summary,
                alerts=tuple(_alert_view(record) for record in alert_records[:500]),
                alerts_truncated=len(alert_records) > 500,
                activities=activity_records[:1000],
                activities_truncated=len(activity_records) > 1000,
                feishu=FeishuCollaborationView(
                    configured=route is not None,
                    route_name=None if route is None else route.chat_name,
                    chat_id_masked=None if route is None else _mask(route.chat_id),
                    thread_bound=thread is not None,
                    last_synced_at=None if thread is None else thread.last_synced_at,
                    notification_state=(None if notification is None else notification.state),
                    last_error_code=(
                        thread.last_error_code
                        if thread is not None and thread.last_error_code is not None
                        else None
                        if notification is None
                        else notification.last_error_code
                    ),
                ),
                latest_evidence_run=(
                    None
                    if evidence_run is None
                    else LatestEvidenceRunView(
                        id=evidence_run.id,
                        state=evidence_run.state,
                        succeeded_count=evidence_run.succeeded_count,
                        missing_count=evidence_run.missing_count,
                        failed_count=evidence_run.failed_count,
                        completed_at=evidence_run.completed_at,
                    )
                ),
            )

    def acknowledge(
        self,
        incident_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
        actor: str,
        request_id: str,
    ) -> IncidentMutationResult:
        return self._change(
            incident_id,
            action="ACKNOWLEDGE",
            expected_version=expected_version,
            payload={"expected_version": expected_version},
            idempotency_key=idempotency_key,
            actor=actor,
            request_id=request_id,
            transition=lambda incident, activity_id, now: acknowledge_incident(
                incident,
                activity_id=activity_id,
                actor=actor,
                now=now,
            ),
        )

    def resolve(
        self,
        incident_id: str,
        *,
        expected_version: int,
        resolution_summary: str,
        idempotency_key: str,
        actor: str,
        request_id: str,
    ) -> IncidentMutationResult:
        return self._change(
            incident_id,
            action="RESOLVE",
            expected_version=expected_version,
            payload={
                "expected_version": expected_version,
                "resolution_summary": resolution_summary,
            },
            idempotency_key=idempotency_key,
            actor=actor,
            request_id=request_id,
            transition=lambda incident, activity_id, now: resolve_incident(
                incident,
                activity_id=activity_id,
                resolution_summary=resolution_summary,
                actor=actor,
                now=now,
            ),
        )

    def _change(
        self,
        incident_id: str,
        *,
        action: IncidentAction,
        expected_version: int,
        payload: dict[str, object],
        idempotency_key: str,
        actor: str,
        request_id: str,
        transition: Callable[[Incident, str, datetime], IncidentChange],
    ) -> IncidentMutationResult:
        scope = f"incident:{incident_id}:{action.casefold()}"
        key_hash = sha256(idempotency_key.encode()).hexdigest()
        fingerprint = _fingerprint(payload)
        try:
            with self._uow_factory() as uow:
                operations = _operations(uow)
                prior = operations.find(
                    scope=scope,
                    idempotency_key_hash=key_hash,
                )
                if prior is not None:
                    return self._replay(uow, prior, fingerprint)
                incidents = _incidents(uow)
                incident = incidents.get(incident_id, for_update=True)
                if incident is None:
                    raise IncidentNotFound()
                if incident.version != expected_version:
                    raise IncidentVersionConflict()
                now = self._clock().astimezone(UTC)
                change = transition(incident, self._id_factory("iact"), now)
                if not incidents.update(change.incident, expected_version=incident.version):
                    raise IncidentVersionConflict()
                incidents.append_activities(change.activities)
                _enqueue_update_notification(
                    _notifications(uow),
                    change=change,
                    notification_id=self._id_factory("ino"),
                    now=now,
                )
                operations.insert(
                    IncidentOperationRecord(
                        id=self._id_factory("iop"),
                        scope=scope,
                        idempotency_key_hash=key_hash,
                        command_fingerprint=fingerprint,
                        action=action,
                        incident_id=incident_id,
                        result_version=change.incident.version,
                        actor=actor,
                        request_id=request_id,
                        summary=change.activities[0].summary,
                        completed_at=now,
                    )
                )
                uow.commit()
                return IncidentMutationResult(incident=change.incident, replayed=False)
        except IntegrityError:
            replay = self._replay_after_conflict(scope, key_hash, fingerprint)
            if replay is not None:
                return replay
            raise

    def _replay(
        self,
        uow: SqlAlchemyUnitOfWork,
        prior: IncidentOperationRecord,
        fingerprint: str,
    ) -> IncidentMutationResult:
        if prior.command_fingerprint != fingerprint:
            raise IncidentIdempotencyConflict()
        incident = _incidents(uow).get(prior.incident_id)
        if incident is None:
            raise IncidentNotFound()
        return IncidentMutationResult(incident=incident, replayed=True)

    def _replay_after_conflict(
        self,
        scope: str,
        key_hash: str,
        fingerprint: str,
    ) -> IncidentMutationResult | None:
        with self._uow_factory() as uow:
            prior = _operations(uow).find(
                scope=scope,
                idempotency_key_hash=key_hash,
            )
            return None if prior is None else self._replay(uow, prior, fingerprint)


def _fingerprint(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode()).hexdigest()


def _enqueue_update_notification(
    repository: IncidentNotificationRepository,
    *,
    change: IncidentChange,
    notification_id: str,
    now: datetime,
) -> None:
    activity = change.activities[0]
    incident = change.incident
    key = sha256(f"{incident.id}\x1f{activity.id}\x1fUPDATE_CARD".encode()).hexdigest()
    repository.enqueue(
        IncidentNotificationRecord(
            id=notification_id,
            incident_id=incident.id,
            activity_id=activity.id,
            notification_key=key,
            kind="UPDATE_CARD",
            state="PENDING",
            payload={
                "incident_id": incident.id,
                "reference": incident.reference,
                "state": incident.state,
                "severity": incident.severity,
                "activity_kind": activity.kind,
                "activity_summary": activity.summary,
            },
            attempt_count=0,
            available_at=now,
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=None,
            feishu_message_id=None,
            created_at=now,
            updated_at=now,
        )
    )


def _incidents(uow: SqlAlchemyUnitOfWork) -> IncidentRepository:
    if uow.incidents is None:
        raise RuntimeError("工作单元没有可用 Incident 仓储")
    return uow.incidents


def _operations(uow: SqlAlchemyUnitOfWork) -> IncidentOperationRepository:
    if uow.incident_operations is None:
        raise RuntimeError("工作单元没有可用 Incident 操作仓储")
    return uow.incident_operations


def _notifications(uow: SqlAlchemyUnitOfWork) -> IncidentNotificationRepository:
    if uow.incident_notifications is None:
        raise RuntimeError("工作单元没有可用 Incident 通知仓储")
    return uow.incident_notifications


def _alerts(uow: SqlAlchemyUnitOfWork) -> AlertRepository:
    if uow.alerts is None:
        raise RuntimeError("工作单元没有可用 Alert 仓储")
    return uow.alerts


def _rules(uow: SqlAlchemyUnitOfWork) -> IncidentRuleRepository:
    if uow.incident_rules is None:
        raise RuntimeError("工作单元没有可用 Incident 规则仓储")
    return uow.incident_rules


def _routes(uow: SqlAlchemyUnitOfWork) -> IncidentNotificationRouteRepository:
    if uow.incident_notification_routes is None:
        raise RuntimeError("工作单元没有可用飞书路由仓储")
    return uow.incident_notification_routes


def _threads(uow: SqlAlchemyUnitOfWork) -> IncidentFeishuThreadRepository:
    if uow.incident_feishu_threads is None:
        raise RuntimeError("工作单元没有可用飞书线程仓储")
    return uow.incident_feishu_threads


def _evidence_runs(uow: SqlAlchemyUnitOfWork) -> EvidenceRunRepository:
    if uow.evidence_runs is None:
        raise RuntimeError("工作单元没有可用取证运行仓储")
    return uow.evidence_runs


def _alert_view(record: AlertRecord) -> IncidentAlertView:
    alert = record.alert
    return IncidentAlertView(
        id=alert.id,
        alert_name=alert.alert_name,
        summary=alert.summary,
        description=alert.description,
        state=alert.state,
        severity=alert.severity,
        environment=alert.environment,
        service=alert.service,
        entity_type=alert.entity_type,
        entity_display_name=alert.entity_display_name,
        source_id=record.source.id,
        source_name=record.source.name,
        first_observed_at=alert.first_observed_at,
        last_observed_at=alert.last_observed_at,
        first_received_at=alert.first_received_at,
        last_received_at=alert.last_received_at,
        resolved_at=alert.resolved_at,
    )


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}…{value[-4:]}"


__all__ = [
    "FeishuCollaborationView",
    "IncidentAlertView",
    "IncidentDetailView",
    "IncidentIdempotencyConflict",
    "IncidentListItem",
    "IncidentMutationResult",
    "IncidentNotFound",
    "IncidentPageView",
    "IncidentService",
    "IncidentStateConflict",
    "IncidentVersionConflict",
    "LatestEvidenceRunView",
]
