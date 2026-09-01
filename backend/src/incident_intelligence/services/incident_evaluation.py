from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict

from incident_intelligence.domain.incident_rule_evaluation import (
    AlertEvaluationFact,
    EvaluationMatch,
    evaluate_rule,
)
from incident_intelligence.domain.incident_rules import IncidentRuleConfig, RuleGroupBy
from incident_intelligence.domain.incidents import (
    Incident,
    IncidentActivityIds,
    IncidentAlertFact,
    IncidentAlertLink,
    create_incident,
    link_alerts,
)
from incident_intelligence.domain.models import Environment
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.alert_center_repository import AlertRepository
from incident_intelligence.persistence.incident_repository import (
    IncidentEvaluationJobRecord,
    IncidentEvaluationJobRepository,
    IncidentRepository,
)
from incident_intelligence.persistence.incident_rule_repository import IncidentRuleRepository
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork


class EvaluationJobResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    outcome: str
    reason_codes: tuple[str, ...]
    incident_ids: tuple[str, ...]
    replayed: bool = False


class IncidentEvaluationService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[IdPrefix], str] = new_id,
        reference_factory: Callable[[datetime], str] | None = None,
        owner: str = "incident-evaluation",
        lease_seconds: int = 60,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory
        self._reference_factory = reference_factory
        self._owner = owner
        self._lease_seconds = lease_seconds

    def process(self, job_id: str) -> EvaluationJobResult:
        now = self._clock().astimezone(UTC)
        with self._uow_factory() as uow:
            jobs = _evaluation_jobs(uow)
            job = jobs.get(job_id, for_update=True)
            if job is None:
                raise RuntimeError("incident_evaluation_job_not_found")
            if job.state == "SUCCEEDED":
                return _completed_result(job)
            if job.state == "PENDING":
                if not jobs.claim_due(
                    job_id,
                    owner=self._owner,
                    now=now,
                    lease_until=now + timedelta(seconds=self._lease_seconds),
                ):
                    raise RuntimeError("incident_evaluation_job_not_claimable")
                job = jobs.get(job_id, for_update=True)
                if job is None:
                    raise RuntimeError("incident_evaluation_job_not_found")
            if job.state != "LEASED" or job.lease_owner != self._owner:
                raise RuntimeError("incident_evaluation_job_not_owned")

            session = uow.session
            if session is None:
                raise RuntimeError("工作单元没有可用数据库会话")
            alerts = AlertRepository(session)
            anchor = alerts.get_evaluation_fact(job.alert_id)
            if anchor is None:
                raise RuntimeError("incident_evaluation_alert_not_found")

            rules = _rules(uow).list_published(limit=101)
            if len(rules) > 100:
                raise RuntimeError("published_incident_rule_limit_exceeded")

            incidents = _incidents(uow)
            affected: list[str] = []
            created = False
            for rule in rules:
                if not _anchor_in_scope(rule.config, anchor):
                    continue
                facts = alerts.list_for_rule_evaluation(
                    environment=rule.config.environment,
                    alert_source_ids=rule.config.alert_source_ids,
                    services=rule.config.services,
                    received_from=anchor.first_received_at
                    - timedelta(minutes=rule.config.window_minutes),
                    received_to=anchor.first_received_at + timedelta(microseconds=1),
                    limit=10_001,
                )
                if len(facts) > 10_000:
                    raise RuntimeError("incident_rule_evaluation_alert_limit_exceeded")
                evaluation = evaluate_rule(rule.config, facts, max_matches=100)
                for match in evaluation.matches:
                    members = _members_for_match(rule.config.group_by, match, facts)
                    if anchor.id not in {member.id for member in members}:
                        continue
                    incident, was_created = self._apply_match(
                        incidents=incidents,
                        alerts=alerts,
                        rule_id=rule.id,
                        rule_version=rule.version,
                        environment=rule.config.environment,
                        group_by=rule.config.group_by,
                        match=match,
                        members=members,
                        now=now,
                    )
                    if incident.id not in affected:
                        affected.append(incident.id)
                    created = created or was_created

            outcome = (
                "INCIDENT_CREATED"
                if created
                else "INCIDENT_UPDATED"
                if affected
                else "NO_MATCH"
            )
            reason_codes = (
                ("published_rule_matched",)
                if affected
                else ("no_published_rule_match",)
            )
            if not jobs.complete(
                job.id,
                owner=self._owner,
                outcome=outcome,
                reason_codes=reason_codes,
                incident_ids=tuple(affected),
                now=now,
            ):
                raise RuntimeError("incident_evaluation_job_lease_lost")
            uow.commit()
            return EvaluationJobResult(
                job_id=job.id,
                outcome=outcome,
                reason_codes=reason_codes,
                incident_ids=tuple(affected),
            )

    def _apply_match(
        self,
        *,
        incidents: IncidentRepository,
        alerts: AlertRepository,
        rule_id: str,
        rule_version: int,
        environment: Environment,
        group_by: RuleGroupBy,
        match: EvaluationMatch,
        members: tuple[AlertEvaluationFact, ...],
        now: datetime,
    ) -> tuple[Incident, bool]:
        existing = incidents.find_unresolved(
            rule_id=rule_id,
            environment=environment,
            group_key=match.group_key,
            for_update=True,
        )
        if existing is None:
            incident_id = self._id_factory("inc")
            reference = (
                self._reference_factory(now)
                if self._reference_factory is not None
                else incidents.next_reference(now)
            )
            change = create_incident(
                incident_id=incident_id,
                reference=reference,
                rule_id=rule_id,
                rule_version=rule_version,
                environment=environment,
                group_by=group_by,
                group_key=match.group_key,
                group_display_name=match.group_display_name,
                alerts=_incident_facts(members),
                created_activity_id=self._id_factory("iact"),
                now=now,
            )
            incidents.insert(change.incident)
            incidents.link_alerts(
                tuple(
                    IncidentAlertLink(
                        incident_id=incident_id,
                        alert_id=alert_id,
                        incident_rule_version=rule_version,
                        first_trigger_window=True,
                        linked_at=now,
                    )
                    for alert_id in change.new_alert_ids
                )
            )
            incidents.append_activities(change.activities)
            return change.incident, True

        existing_ids = incidents.list_alert_ids(existing.id)
        all_facts = {
            fact.id: fact
            for fact in (*alerts.list_evaluation_facts_by_ids(existing_ids), *members)
        }
        change = link_alerts(
            existing,
            existing_alert_ids=existing_ids,
            alerts=_incident_facts(tuple(all_facts.values())),
            activity_ids=IncidentActivityIds(
                alerts_linked=self._id_factory("iact"),
                severity_escalated=self._id_factory("iact"),
                all_alerts_recovered=self._id_factory("iact"),
            ),
            now=now,
        )
        if change.changed and not incidents.update(
            change.incident,
            expected_version=existing.version,
        ):
            raise RuntimeError("incident_concurrent_update")
        incidents.link_alerts(
            tuple(
                IncidentAlertLink(
                    incident_id=existing.id,
                    alert_id=alert_id,
                    incident_rule_version=rule_version,
                    first_trigger_window=False,
                    linked_at=now,
                )
                for alert_id in change.new_alert_ids
            )
        )
        incidents.append_activities(change.activities)
        return change.incident, False


def _anchor_in_scope(
    config: IncidentRuleConfig,
    anchor: AlertEvaluationFact,
) -> bool:
    return (
        anchor.environment == config.environment
        and (not config.alert_source_ids or anchor.alert_source_id in config.alert_source_ids)
        and (not config.services or anchor.service in config.services)
        and (config.group_by != "SERVICE" or anchor.service is not None)
    )


def _members_for_match(
    group_by: str,
    match: EvaluationMatch,
    facts: tuple[AlertEvaluationFact, ...],
) -> tuple[AlertEvaluationFact, ...]:
    return tuple(
        fact
        for fact in facts
        if match.window_start < fact.first_received_at <= match.window_end
        and (fact.service if group_by == "SERVICE" else fact.entity_key) == match.group_key
    )


def _incident_facts(
    facts: tuple[AlertEvaluationFact, ...],
) -> tuple[IncidentAlertFact, ...]:
    return tuple(
        IncidentAlertFact(
            id=fact.id,
            alert_name=fact.alert_name,
            state=fact.state,
            severity=fact.severity,
            first_received_at=fact.first_received_at,
        )
        for fact in facts
    )


def _completed_result(job: IncidentEvaluationJobRecord) -> EvaluationJobResult:
    return EvaluationJobResult(
        job_id=job.id,
        outcome=job.outcome or "NO_MATCH",
        reason_codes=job.reason_codes,
        incident_ids=job.incident_ids,
        replayed=True,
    )


def _evaluation_jobs(uow: SqlAlchemyUnitOfWork) -> IncidentEvaluationJobRepository:
    if uow.incident_evaluation_jobs is None:
        raise RuntimeError("工作单元没有可用 Incident 评估任务仓储")
    return uow.incident_evaluation_jobs


def _rules(uow: SqlAlchemyUnitOfWork) -> IncidentRuleRepository:
    if uow.incident_rules is None:
        raise RuntimeError("工作单元没有可用 Incident 规则仓储")
    return uow.incident_rules


def _incidents(uow: SqlAlchemyUnitOfWork) -> IncidentRepository:
    if uow.incidents is None:
        raise RuntimeError("工作单元没有可用 Incident 仓储")
    return uow.incidents
