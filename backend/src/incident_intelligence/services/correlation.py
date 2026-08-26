from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.domain.catalog import normalize_symptom
from incident_intelligence.domain.correlation import CorrelationContext, decide_correlation
from incident_intelligence.domain.enums import CatalogState, CorrelationOutcome
from incident_intelligence.ids import new_id
from incident_intelligence.persistence.catalog_repository import ServiceCatalogRepository
from incident_intelligence.persistence.correlation_repository import CorrelationRepository
from incident_intelligence.persistence.models import (
    CorrelationDecisionRow,
    CorrelationJobRow,
    IncidentAlertLinkRow,
    IncidentRow,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.correlation_jobs import (
    CorrelationJobLease,
    CorrelationJobNotRetryable,
)

CORRELATION_WINDOW_SECONDS = 900
CORRELATION_CANDIDATE_QUERY_LIMIT = 21
CORRELATION_RULE_VERSION = "correlation.v1"


class CorrelationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(pattern=r"^cdec_[0-9a-f]{32}$")
    alert_id: str = Field(pattern=r"^alt_[0-9a-f]{32}$")
    incident_id: str | None = Field(default=None, pattern=r"^inc_[0-9a-f]{32}$")
    outcome: CorrelationOutcome
    explanation: str = Field(min_length=1, max_length=500)


class CorrelationService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def process(self, lease: CorrelationJobLease) -> CorrelationResult:
        now = self._clock().astimezone(UTC)
        with self._uow_factory() as uow:
            correlation = _correlation(uow)
            catalog = _catalog(uow)
            job = correlation.find_job(lease.id, for_update=True)
            if (
                job is None
                or job.state != "LEASED"
                or job.lease_owner != lease.lease_owner
                or job.alert_id != lease.alert_id
                or job.alert_version != lease.alert_version
            ):
                raise CorrelationJobNotRetryable()

            alert = correlation.find_alert_for_update(job.alert_id)
            if alert is None:
                raise RuntimeError("correlation_alert_not_found")
            signal = correlation.find_signal(alert.signal_event_id)
            if signal is None:
                raise RuntimeError("correlation_signal_not_found")
            existing_link = correlation.find_link_by_alert(alert.id)
            service = alert.service
            catalog_entry = (
                None
                if service is None
                else catalog.find_service_identity(service, alert.environment, for_update=True)
            )

            exact_ids: tuple[str, ...] = ()
            dependency_ids: tuple[str, ...] = ()
            symptom = normalize_symptom(signal.facts.get("symptom"))
            eligible_for_candidates = (
                alert.version == job.alert_version
                and existing_link is None
                and alert.state == "ACTIVE"
                and alert.severity in {"critical", "high"}
                and alert.environment == "production"
                and service is not None
                and catalog_entry is not None
                and catalog_entry.state == "ACTIVE"
            )
            if eligible_for_candidates:
                assert service is not None
                exact_ids = tuple(
                    row.id
                    for row in correlation.exact_candidates(
                        service=service,
                        environment=alert.environment,
                        observed_at=alert.last_observed_at,
                        window_seconds=CORRELATION_WINDOW_SECONDS,
                        limit=CORRELATION_CANDIDATE_QUERY_LIMIT,
                    )
                )
                if not exact_ids and symptom is not None and catalog_entry is not None:
                    service_names = correlation.dependency_service_names(catalog_entry.id)
                    dependency_ids = tuple(
                        incident.id
                        for incident, candidate_signal in correlation.dependency_candidates(
                            services=service_names,
                            environment=alert.environment,
                            observed_at=alert.last_observed_at,
                            window_seconds=CORRELATION_WINDOW_SECONDS,
                            limit=CORRELATION_CANDIDATE_QUERY_LIMIT,
                        )
                        if normalize_symptom(candidate_signal.facts.get("symptom")) == symptom
                    )

            draft = decide_correlation(
                CorrelationContext.model_validate(
                    {
                        "alert_version": job.alert_version,
                        "current_alert_version": alert.version,
                        "alert_state": alert.state,
                        "severity": alert.severity,
                        "environment": alert.environment,
                        "catalog_state": (
                            None if catalog_entry is None else CatalogState(catalog_entry.state)
                        ),
                        "existing_incident_id": (
                            None if existing_link is None else existing_link.incident_id
                        ),
                        "exact_candidate_ids": exact_ids,
                        "dependency_candidate_ids": dependency_ids,
                    }
                )
            )

            incident_id = draft.selected_incident_id
            relation: str | None = None
            if draft.action == "CREATE":
                incident_id = new_id("inc")
                correlation.add_incident(
                    IncidentRow(
                        id=incident_id,
                        primary_alert_id=alert.id,
                        state="DETECTED",
                        title=alert.title,
                        severity=alert.severity,
                        service=alert.service,
                        environment=alert.environment,
                        detected_at=alert.last_observed_at,
                        state_changed_at=now,
                        resolved_at=None,
                        closed_at=None,
                        created_at=now,
                        version=1,
                    )
                )
                relation = "PRIMARY"
            elif draft.action == "LINK" and incident_id is not None:
                incident = correlation.find_incident_for_update(incident_id)
                if incident is None:
                    raise RuntimeError("correlation_candidate_not_found")
                if alert.severity == "critical" and incident.severity == "high":
                    incident.severity = "critical"
                    incident.version += 1
                    correlation.flush()
                relation = "RELATED"

            decision_id = new_id("cdec")
            correlation.add_decision(
                CorrelationDecisionRow(
                    id=decision_id,
                    job_id=job.id,
                    alert_id=alert.id,
                    alert_version=job.alert_version,
                    incident_id=incident_id,
                    outcome=draft.outcome.value,
                    rule_version=CORRELATION_RULE_VERSION,
                    reason_codes=list(draft.reason_codes),
                    facts={
                        "service": alert.service,
                        "environment": alert.environment,
                        "severity": alert.severity,
                        "symptom": symptom,
                        "window_seconds": CORRELATION_WINDOW_SECONDS,
                        "candidate_count": len(draft.candidate_incident_ids),
                    },
                    candidate_incident_ids=list(draft.candidate_incident_ids),
                    explanation=draft.explanation,
                    created_at=now,
                )
            )
            if relation is not None and incident_id is not None:
                correlation.add_link(
                    IncidentAlertLinkRow(
                        incident_id=incident_id,
                        alert_id=alert.id,
                        relation=relation,
                        decision_id=decision_id,
                        linked_at=now,
                        created_at=now,
                    )
                )

            job.state = "SUCCEEDED"
            job.lease_owner = None
            job.lease_expires_at = None
            job.last_error_code = None
            job.updated_at = now
            correlation.flush()
            uow.commit()
            return CorrelationResult(
                decision_id=decision_id,
                alert_id=alert.id,
                incident_id=incident_id,
                outcome=draft.outcome,
                explanation=draft.explanation,
            )


class CorrelationResourceNotFound(Exception):
    pass


class CorrelationReadService:
    def __init__(self, *, uow_factory: Callable[[], SqlAlchemyUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def get_alert_correlation(self, alert_id: str) -> dict[str, object]:
        with self._uow_factory() as uow:
            repository = _correlation(uow)
            job = repository.find_latest_job_for_alert(alert_id)
            if job is None:
                raise CorrelationResourceNotFound()
            decision = repository.find_decision_for_job(job.id)
            incident = (
                None
                if decision is None or decision.incident_id is None
                else repository.find_incident_for_update(decision.incident_id)
            )
            return {
                "alert_id": alert_id,
                "job": _job_view(job),
                "incident": None if incident is None else _incident_view(incident),
                "decision": None if decision is None else _decision_view(decision),
            }

    def list_jobs(
        self,
        *,
        state: str | None,
        limit: int,
        offset: int,
    ) -> tuple[dict[str, object], ...]:
        with self._uow_factory() as uow:
            return tuple(
                _job_view(row)
                for row in _correlation(uow).list_jobs(
                    state=state,
                    limit=limit,
                    offset=offset,
                )
            )

    def get_job(self, job_id: str) -> dict[str, object]:
        with self._uow_factory() as uow:
            row = _correlation(uow).find_job(job_id)
            if row is None:
                raise CorrelationResourceNotFound()
            return _job_view(row)


def _job_view(row: CorrelationJobRow) -> dict[str, object]:
    return {
        "id": row.id,
        "alert_version": row.alert_version,
        "state": row.state,
        "attempts": row.attempts,
        "available_at": row.available_at,
        "last_error_code": row.last_error_code,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _incident_view(row: IncidentRow) -> dict[str, object]:
    return {
        "id": row.id,
        "state": row.state,
        "severity": row.severity,
        "service": row.service,
        "environment": row.environment,
        "detected_at": row.detected_at,
        "version": row.version,
    }


def _decision_view(row: CorrelationDecisionRow) -> dict[str, object]:
    return {
        "id": row.id,
        "outcome": row.outcome,
        "rule_version": row.rule_version,
        "reason_codes": tuple(row.reason_codes),
        "facts": row.facts,
        "candidate_incident_ids": tuple(row.candidate_incident_ids),
        "explanation": row.explanation,
        "created_at": row.created_at,
    }


def _correlation(uow: SqlAlchemyUnitOfWork) -> CorrelationRepository:
    if uow.correlation is None:
        raise RuntimeError("工作单元没有可用关联仓储")
    return uow.correlation


def _catalog(uow: SqlAlchemyUnitOfWork) -> ServiceCatalogRepository:
    if uow.catalog is None:
        raise RuntimeError("工作单元没有可用服务目录仓储")
    return uow.catalog
