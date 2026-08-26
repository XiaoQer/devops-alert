from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.domain.alert_grouping import (
    AlertGroupCandidate,
    GroupingContext,
    ResourceIdentity,
    decide_alert_group,
    derive_resource_identity,
)
from incident_intelligence.domain.catalog import normalize_symptom
from incident_intelligence.domain.enums import CatalogState
from incident_intelligence.domain.problem_signatures import (
    ProblemSignature,
    derive_problem_signature,
)
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.alert_group_repository import AlertGroupRepository
from incident_intelligence.persistence.catalog_repository import ServiceCatalogRepository
from incident_intelligence.persistence.models import (
    AlertGroupingJobRow,
    AlertGroupMemberRow,
    AlertGroupRow,
    AlertRow,
)
from incident_intelligence.persistence.repositories import RecordRepositories
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_grouping_jobs import (
    AlertGroupingJobLease,
    AlertGroupingJobNotRetryable,
)

GROUPING_RULE_VERSION = "alert-grouping.v2"
GROUPING_CANDIDATE_LIMIT = 21
STORM_MEMBER_THRESHOLD = 20
STORM_WINDOW_SECONDS = 60
STORM_CLEAR_SECONDS = 300
GroupingResultAction = Literal["CREATE_GROUP", "JOIN_GROUP", "KEEP_GROUP", "SUPERSEDED"]


class AlertGroupingResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(pattern=r"^agj_[0-9a-f]{32}$")
    alert_id: str = Field(pattern=r"^alt_[0-9a-f]{32}$")
    alert_cycle: int = Field(ge=1)
    group_id: str | None = Field(default=None, pattern=r"^agr_[0-9a-f]{32}$")
    action: GroupingResultAction
    reason_code: str = Field(min_length=1, max_length=64)


class AlertGroupingService:
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

    def process(self, lease: AlertGroupingJobLease) -> AlertGroupingResult:
        now = self._clock().astimezone(UTC)
        with self._uow_factory() as uow:
            repository = _groups(uow)
            job = repository.find_grouping_job(lease.id, for_update=True)
            if (
                job is None
                or job.state != "LEASED"
                or job.lease_owner != lease.lease_owner
                or job.alert_id != lease.alert_id
                or job.alert_cycle != lease.alert_cycle
                or job.alert_version != lease.alert_version
            ):
                raise AlertGroupingJobNotRetryable()

            alert = repository.find_alert_for_update(job.alert_id)
            if alert is None:
                raise RuntimeError("alert_grouping_alert_not_found")
            if alert.cycle != job.alert_cycle or alert.version != job.alert_version:
                _complete_job(job, now)
                repository.flush()
                uow.commit()
                return AlertGroupingResult(
                    job_id=job.id,
                    alert_id=job.alert_id,
                    alert_cycle=job.alert_cycle,
                    action="SUPERSEDED",
                    reason_code="alert_version_superseded",
                )

            signal = repository.find_signal(alert.signal_event_id)
            if signal is None:
                raise RuntimeError("alert_grouping_signal_not_found")
            symptom = normalize_symptom(signal.facts.get("symptom")) or "unknown"
            service = alert.service
            signature_facts = dict(signal.facts)
            if service is not None:
                signature_facts["service"] = service
            signature = derive_problem_signature(
                alert_source_id=alert.alert_source_id,
                problem_type=signal.facts.get("alertname") or alert.title,
                symptom=symptom,
                environment=alert.environment,
                facts=signature_facts,
            )
            catalog_entry = (
                None
                if service is None
                else _catalog(uow).find_service_identity(
                    service, alert.environment, for_update=True
                )
            )
            existing_member = repository.find_member(alert.id, alert.cycle, for_update=True)
            existing_link = repository.find_incident_link(alert.id)
            candidates = repository.active_candidates(
                problem_key=signature.problem_key,
                limit=GROUPING_CANDIDATE_LIMIT,
            )
            decision = decide_alert_group(
                GroupingContext.model_validate(
                    {
                        "alert_id": alert.id,
                        "service": alert.service,
                        "problem_key": signature.problem_key,
                        "window_seconds": signature.window_seconds,
                        "environment": alert.environment,
                        "symptom": symptom,
                        "observed_at": alert.last_observed_at,
                        "catalog_state": (
                            None if catalog_entry is None else CatalogState(catalog_entry.state)
                        ),
                        "existing_group_id": (
                            None if existing_member is None else existing_member.alert_group_id
                        ),
                        "alert_incident_id": (
                            None if existing_link is None else existing_link.incident_id
                        ),
                        "candidates": tuple(_candidate(row) for row in candidates),
                    }
                )
            )
            identity = derive_resource_identity(signal.facts, alert.id)

            if decision.action == "CREATE_GROUP":
                group = _new_group(
                    group_id=self._id_factory("agr"),
                    alert=alert,
                    signature=signature,
                    symptom=symptom,
                    reason_code=decision.reason_codes[0],
                    explanation=decision.explanation,
                    incident_id=(None if existing_link is None else existing_link.incident_id),
                    now=now,
                )
                repository.add_group(group)
                member = _new_member(group.id, alert, identity, decision.reason_codes[0], now)
                repository.add_member(member)
            else:
                if decision.selected_group_id is None:
                    raise RuntimeError("alert_grouping_selected_group_missing")
                selected_group = repository.find_group(decision.selected_group_id, for_update=True)
                if selected_group is None:
                    raise RuntimeError("alert_grouping_candidate_not_found")
                group = selected_group
                if existing_link is not None and group.incident_id is None:
                    group.incident_id = existing_link.incident_id
                if existing_member is None:
                    member = _new_member(group.id, alert, identity, decision.reason_codes[0], now)
                    repository.add_member(member)
                else:
                    member = existing_member
                    member.current_alert_version = alert.version
                    member.current_state = alert.state
                    member.current_severity = alert.severity
                    member.resource_type = identity.resource_type
                    member.resource_name = identity.resource_name
                    member.resource_key = identity.resource_key
                    member.updated_at = now
                    repository.flush()
                _refresh_group(
                    repository,
                    group,
                    reason_codes=list(decision.reason_codes),
                    explanation=decision.explanation,
                    now=now,
                )

            if _requires_correlation(group, catalog_entry):
                repository.schedule_correlation(
                    group,
                    target_version=group.version,
                    now=now,
                )

            _append_audit(
                _records(uow),
                audit_id=self._id_factory("aud"),
                group=group,
                alert=alert,
                reason_code=decision.reason_codes[0],
                action=decision.action,
                now=now,
            )
            _complete_job(job, now)
            repository.flush()
            uow.commit()
            return AlertGroupingResult(
                job_id=job.id,
                alert_id=alert.id,
                alert_cycle=alert.cycle,
                group_id=group.id,
                action=decision.action,
                reason_code=decision.reason_codes[0],
            )


def _new_group(
    *,
    group_id: str,
    alert: AlertRow,
    signature: ProblemSignature,
    symptom: str,
    reason_code: str,
    explanation: str,
    incident_id: str | None,
    now: datetime,
) -> AlertGroupRow:
    active_count = 1 if alert.state == "ACTIVE" else 0
    return AlertGroupRow(
        id=group_id,
        state="ACTIVE" if active_count else "RESOLVED",
        storm_state="NORMAL",
        rule_version=GROUPING_RULE_VERSION,
        service=alert.service,
        entity_type=alert.entity_type,
        entity_key=alert.entity_key,
        entity_display_name=alert.entity_display_name,
        problem_key=signature.problem_key,
        problem_type=signature.problem_type,
        scope_type=signature.scope_type,
        scope_key=signature.scope_key,
        scope_display_name=signature.scope_display_name,
        signature_version=signature.version,
        environment=alert.environment,
        symptom=symptom,
        title=alert.title,
        severity=alert.severity,
        representative_alert_id=alert.id,
        incident_id=incident_id,
        first_observed_at=alert.first_observed_at,
        last_observed_at=alert.last_observed_at,
        state_changed_at=now,
        last_member_at=now,
        active_count=active_count,
        total_count=1,
        impacted_resource_count=1,
        desired_correlation_version=0,
        reason_codes=[reason_code],
        explanation=explanation,
        created_at=now,
        updated_at=now,
        version=1,
    )


def _new_member(
    group_id: str,
    alert: AlertRow,
    identity: ResourceIdentity,
    reason_code: str,
    now: datetime,
) -> AlertGroupMemberRow:
    return AlertGroupMemberRow(
        alert_group_id=group_id,
        alert_id=alert.id,
        alert_cycle=alert.cycle,
        joined_alert_version=alert.version,
        current_alert_version=alert.version,
        current_state=alert.state,
        current_severity=alert.severity,
        resource_type=identity.resource_type,
        resource_name=identity.resource_name,
        resource_key=identity.resource_key,
        reason_code=reason_code,
        joined_at=now,
        updated_at=now,
    )


def _refresh_group(
    repository: AlertGroupRepository,
    group: AlertGroupRow,
    *,
    reason_codes: list[str],
    explanation: str,
    now: datetime,
) -> None:
    aggregate = repository.aggregate_group(
        group.id,
        recent_since=now - timedelta(seconds=STORM_WINDOW_SECONDS),
    )
    if aggregate is None:
        raise RuntimeError("alert_grouping_group_has_no_members")

    previous_state = group.state
    group.state = "ACTIVE" if aggregate.active_count else "RESOLVED"
    if group.state != previous_state:
        group.state_changed_at = now
    group.active_count = aggregate.active_count
    group.total_count = aggregate.total_count
    group.impacted_resource_count = aggregate.impacted_resource_count
    group.first_observed_at = aggregate.first_observed_at
    group.last_observed_at = aggregate.last_observed_at
    group.last_member_at = aggregate.last_member_at
    group.severity = aggregate.representative_severity
    group.representative_alert_id = aggregate.representative_alert_id
    group.title = aggregate.representative_title
    if aggregate.recent_member_count >= STORM_MEMBER_THRESHOLD:
        group.storm_state = "STORM"
    elif group.storm_state == "STORM" and group.last_member_at <= now - timedelta(
        seconds=STORM_CLEAR_SECONDS
    ):
        group.storm_state = "NORMAL"
    group.rule_version = GROUPING_RULE_VERSION
    group.reason_codes = reason_codes
    group.explanation = explanation
    group.updated_at = now
    group.version += 1


def _candidate(row: AlertGroupRow) -> AlertGroupCandidate:
    return AlertGroupCandidate.model_validate(
        {
            "id": row.id,
            "service": row.service,
            "problem_key": row.problem_key,
            "environment": row.environment,
            "symptom": row.symptom,
            "last_observed_at": row.last_observed_at,
            "incident_id": row.incident_id,
        }
    )


def _append_audit(
    records: RecordRepositories,
    *,
    audit_id: str,
    group: AlertGroupRow,
    alert: AlertRow,
    reason_code: str,
    action: str,
    now: datetime,
) -> None:
    records.add_audit(
        audit_id=audit_id,
        actor="alert-grouping-worker",
        action="alert.grouped",
        resource_type="alert_group",
        resource_id=group.id,
        request_id=group.id,
        details={
            "reason_code": reason_code,
            "rule_version": GROUPING_RULE_VERSION,
            "grouping_action": action,
            "alert_id": alert.id,
        },
        created_at=now,
    )


def _requires_correlation(group: AlertGroupRow, catalog_entry: object | None) -> bool:
    if group.incident_id is not None:
        return True
    base_eligible = (
        group.state == "ACTIVE"
        and group.severity in {"critical", "high"}
        and group.environment == "production"
    )
    if group.service is None:
        return base_eligible
    return (
        base_eligible
        and catalog_entry is not None
        and getattr(catalog_entry, "state", None) == "ACTIVE"
    )


def _complete_job(job: AlertGroupingJobRow, now: datetime) -> None:
    job.state = "SUCCEEDED"
    job.lease_owner = None
    job.lease_expires_at = None
    job.last_error_code = None
    job.updated_at = now


def _groups(uow: SqlAlchemyUnitOfWork) -> AlertGroupRepository:
    if uow.alert_groups is None:
        raise RuntimeError("工作单元没有可用告警组仓储")
    return uow.alert_groups


def _catalog(uow: SqlAlchemyUnitOfWork) -> ServiceCatalogRepository:
    if uow.catalog is None:
        raise RuntimeError("工作单元没有可用服务目录仓储")
    return uow.catalog


def _records(uow: SqlAlchemyUnitOfWork) -> RecordRepositories:
    if uow.records is None:
        raise RuntimeError("工作单元没有可用记录仓储")
    return uow.records
