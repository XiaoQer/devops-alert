from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.domain.alert_group_correlation import (
    AlertGroupCorrelationContext,
    decide_alert_group_correlation,
)
from incident_intelligence.domain.enums import CatalogState, CorrelationOutcome
from incident_intelligence.ids import new_id
from incident_intelligence.persistence.alert_group_repository import AlertGroupRepository
from incident_intelligence.persistence.catalog_repository import ServiceCatalogRepository
from incident_intelligence.persistence.correlation_repository import CorrelationRepository
from incident_intelligence.persistence.models import (
    AlertGroupDecisionRow,
    IncidentAlertLinkRow,
    IncidentRow,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_group_correlation_jobs import (
    AlertGroupCorrelationJobNotRetryable,
    AlertGroupCorrelationLease,
)

CORRELATION_WINDOW_SECONDS = 900
CORRELATION_CANDIDATE_LIMIT = 21
GROUP_CORRELATION_RULE_VERSION = "group-correlation.v1"


class AlertGroupCorrelationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    decision_id: str = Field(pattern=r"^gdec_[0-9a-f]{32}$")
    alert_group_id: str = Field(pattern=r"^agr_[0-9a-f]{32}$")
    incident_id: str | None = Field(default=None, pattern=r"^inc_[0-9a-f]{32}$")
    outcome: CorrelationOutcome
    linked_alert_count: int = Field(ge=0)


class AlertGroupCorrelationService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def process(self, lease: AlertGroupCorrelationLease) -> AlertGroupCorrelationResult:
        now = self._clock().astimezone(UTC)
        with self._uow_factory() as uow:
            groups = _groups(uow)
            correlation = _correlation(uow)
            job = groups.find_correlation_job(lease.id, for_update=True)
            if (
                job is None
                or job.state != "LEASED"
                or job.lease_owner != lease.lease_owner
                or job.alert_group_id != lease.alert_group_id
                or job.target_group_version != lease.target_group_version
            ):
                raise AlertGroupCorrelationJobNotRetryable()
            group = groups.find_group(job.alert_group_id, for_update=True)
            if group is None:
                raise RuntimeError("alert_group_correlation_group_not_found")
            service = group.service
            catalog_entry = (
                None
                if service is None
                else _catalog(uow).find_service_identity(
                    service, group.environment, for_update=True
                )
            )

            exact_ids: tuple[str, ...] = ()
            eligible = (
                group.version == job.target_group_version
                and group.incident_id is None
                and group.state in {"FORMING", "ACTIVE"}
                and group.severity in {"critical", "high"}
                and group.environment == "production"
                and service is not None
                and catalog_entry is not None
                and catalog_entry.state == "ACTIVE"
            )
            if eligible:
                assert service is not None
                exact_ids = tuple(
                    row.id
                    for row in correlation.exact_candidates(
                        service=service,
                        environment=group.environment,
                        observed_at=group.last_observed_at,
                        window_seconds=CORRELATION_WINDOW_SECONDS,
                        limit=CORRELATION_CANDIDATE_LIMIT,
                    )
                )

            draft = decide_alert_group_correlation(
                AlertGroupCorrelationContext.model_validate(
                    {
                        "target_group_version": job.target_group_version,
                        "current_group_version": group.version,
                        "group_state": group.state,
                        "severity": group.severity,
                        "environment": group.environment,
                        "service_present": service is not None,
                        "catalog_state": (
                            None if catalog_entry is None else CatalogState(catalog_entry.state)
                        ),
                        "existing_incident_id": group.incident_id,
                        "exact_candidate_ids": exact_ids,
                        "dependency_candidate_ids": (),
                    }
                )
            )

            incident_id = draft.selected_incident_id
            created = False
            if draft.action == "CREATE":
                incident_id = new_id("inc")
                groups.add_incident(
                    IncidentRow(
                        id=incident_id,
                        primary_alert_id=group.representative_alert_id,
                        state="DETECTED",
                        title=group.title,
                        severity=group.severity,
                        service=group.service,
                        environment=group.environment,
                        detected_at=group.last_observed_at,
                        state_changed_at=now,
                        resolved_at=None,
                        closed_at=None,
                        created_at=now,
                        version=1,
                    )
                )
                created = True
            elif draft.action == "LINK" and incident_id is not None:
                incident = correlation.find_incident_for_update(incident_id)
                if incident is None:
                    raise RuntimeError("alert_group_correlation_candidate_not_found")
                if group.severity == "critical" and incident.severity == "high":
                    incident.severity = "critical"
                    incident.version += 1

            decision_id = new_id("gdec")
            groups.add_group_decision(
                AlertGroupDecisionRow(
                    id=decision_id,
                    job_id=job.id,
                    alert_group_id=group.id,
                    group_version=job.target_group_version,
                    incident_id=incident_id,
                    outcome=draft.outcome.value,
                    rule_version=GROUP_CORRELATION_RULE_VERSION,
                    reason_codes=list(draft.reason_codes),
                    facts={
                        "service": group.service,
                        "environment": group.environment,
                        "severity": group.severity,
                        "symptom": group.symptom,
                        "problem_type": group.problem_type,
                        "scope_type": group.scope_type,
                        "scope_display_name": group.scope_display_name,
                        "signature_version": group.signature_version,
                        "window_seconds": CORRELATION_WINDOW_SECONDS,
                        "member_count": group.total_count,
                    },
                    candidate_incident_ids=list(draft.candidate_incident_ids),
                    explanation=draft.explanation,
                    created_at=now,
                )
            )

            linked_count = 0
            if incident_id is not None:
                for member in groups.list_members(group.id):
                    existing = groups.find_incident_link(member.alert_id)
                    if existing is not None:
                        if existing.incident_id != incident_id:
                            raise RuntimeError("alert_group_member_incident_conflict")
                        continue
                    groups.add_incident_link(
                        IncidentAlertLinkRow(
                            incident_id=incident_id,
                            alert_id=member.alert_id,
                            relation=(
                                "PRIMARY"
                                if created and member.alert_id == group.representative_alert_id
                                else "RELATED"
                            ),
                            decision_id=None,
                            group_decision_id=decision_id,
                            linked_at=now,
                            created_at=now,
                        )
                    )
                    linked_count += 1
                group.incident_id = incident_id

            job.state = "SUCCEEDED"
            job.active_slot = None
            job.lease_owner = None
            job.lease_expires_at = None
            job.last_error_code = None
            job.updated_at = now
            groups.flush()
            if group.desired_correlation_version > job.target_group_version:
                groups.schedule_correlation(
                    group,
                    target_version=group.desired_correlation_version,
                    now=now,
                )
            uow.commit()
            return AlertGroupCorrelationResult(
                decision_id=decision_id,
                alert_group_id=group.id,
                incident_id=incident_id,
                outcome=draft.outcome,
                linked_alert_count=linked_count,
            )


def _groups(uow: SqlAlchemyUnitOfWork) -> AlertGroupRepository:
    if uow.alert_groups is None:
        raise RuntimeError("工作单元没有可用告警组仓储")
    return uow.alert_groups


def _correlation(uow: SqlAlchemyUnitOfWork) -> CorrelationRepository:
    if uow.correlation is None:
        raise RuntimeError("工作单元没有可用关联仓储")
    return uow.correlation


def _catalog(uow: SqlAlchemyUnitOfWork) -> ServiceCatalogRepository:
    if uow.catalog is None:
        raise RuntimeError("工作单元没有可用服务目录仓储")
    return uow.catalog
