from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.domain.alert_event_clustering import (
    CandidateContext,
    CandidateScore,
    MembershipDecision,
    decide_membership,
    score_candidate,
)
from incident_intelligence.domain.alert_event_lifecycle import (
    AlertEventState,
    LifecycleContext,
    decide_lifecycle,
)
from incident_intelligence.domain.alert_event_profiles import (
    AlertEventProfile,
    ConfirmedEventMember,
    build_event_profile,
)
from incident_intelligence.domain.alert_grouping import (
    ResourceIdentity,
    derive_resource_identity,
)
from incident_intelligence.domain.alert_text_similarity import normalize_alert_text
from incident_intelligence.domain.catalog import normalize_symptom
from incident_intelligence.domain.problem_signatures import (
    ProblemSignature,
    derive_problem_signature,
)
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.alert_group_repository import (
    AlertEventCandidateRecord,
    AlertEventMemberFact,
    AlertGroupRepository,
)
from incident_intelligence.persistence.catalog_repository import ServiceCatalogRepository
from incident_intelligence.persistence.models import (
    AlertGroupingJobRow,
    AlertGroupMemberRow,
    AlertGroupRow,
    AlertRow,
    SignalEventRow,
)
from incident_intelligence.persistence.repositories import RecordRepositories
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_grouping_jobs import (
    AlertGroupingJobLease,
    AlertGroupingJobNotRetryable,
)
from incident_intelligence.services.text_similarity import TextSimilarityService

GROUPING_RULE_VERSION = "alert-event-clustering.v1"
GROUPING_CANDIDATE_LIMIT = 50
STORM_MEMBER_THRESHOLD = 20
STORM_WINDOW_SECONDS = 60
STORM_CLEAR_SECONDS = 300
GroupingResultAction = Literal[
    "CREATE_GROUP",
    "JOIN_GROUP",
    "KEEP_GROUP",
    "PENDING_CONFIRMATION",
    "SUPERSEDED",
]


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
        text_similarity: TextSimilarityService | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory
        self._text_similarity = text_similarity or TextSimilarityService()

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
            identity = derive_resource_identity(signal.facts, alert.id)
            scores: tuple[CandidateScore, ...] = ()
            group: AlertGroupRow
            action: GroupingResultAction

            if existing_member is not None:
                existing_group = repository.find_group(
                    existing_member.alert_group_id, for_update=True
                )
                if existing_group is None:
                    raise RuntimeError("alert_grouping_candidate_not_found")
                group = existing_group
                decision = MembershipDecision(
                    outcome="AUTO_JOIN",
                    selected_group_id=group.id,
                    candidate_group_ids=(group.id,),
                    reason_codes=("alert_already_grouped",),
                    explanation="该告警轮次已经属于现有告警事件, 保持原成员关系。",
                )
                action = "KEEP_GROUP"
                existing_member.current_alert_version = alert.version
                existing_member.current_state = alert.state
                existing_member.current_severity = alert.severity
                existing_member.resource_type = identity.resource_type
                existing_member.resource_name = identity.resource_name
                existing_member.resource_key = identity.resource_key
                existing_member.updated_at = now
                repository.flush()
                refresh_group_membership(
                    repository,
                    group,
                    reason_codes=list(decision.reason_codes),
                    explanation=decision.explanation,
                    now=now,
                )
                group.profile_version += 1
                save_current_event_profile(repository, group, now=now)
                schedule_event_lifecycle(repository, group, now=now)
            else:
                candidate_batch = repository.candidate_events(
                    environment=alert.environment,
                    observed_at=alert.last_observed_at,
                    received_at=signal.received_at,
                    entity_key=alert.entity_key,
                    service=alert.service,
                    problem_key=signature.problem_key,
                    alert_source_id=alert.alert_source_id,
                    limit=GROUPING_CANDIDATE_LIMIT,
                )
                scores = tuple(
                    self._score_candidate(
                        alert=alert,
                        signal=signal,
                        symptom=symptom,
                        signature=signature,
                        identity=identity,
                        candidate=candidate,
                        repository=repository,
                    )
                    for candidate in candidate_batch.items
                )
                decision = _late_membership_decision(
                    alert=alert,
                    signal=signal,
                    signature=signature,
                    candidates=candidate_batch.items,
                    truncated=candidate_batch.truncated,
                ) or decide_membership(scores, candidates_truncated=candidate_batch.truncated)
                if decision.outcome == "PENDING_CONFIRMATION":
                    repository.save_membership_decision(
                        decision_id=self._id_factory("amd"),
                        alert=alert,
                        state="PENDING",
                        decision=decision,
                        scores=scores,
                        selected_group_id=None,
                        selected_group_version=None,
                        created_at=now,
                    )
                    _append_pending_audit(
                        _records(uow),
                        audit_id=self._id_factory("aud"),
                        alert=alert,
                        reason_code=decision.reason_codes[0],
                        now=now,
                    )
                    _complete_job(job, now)
                    repository.flush()
                    uow.commit()
                    return AlertGroupingResult(
                        job_id=job.id,
                        alert_id=alert.id,
                        alert_cycle=alert.cycle,
                        group_id=None,
                        action="PENDING_CONFIRMATION",
                        reason_code=decision.reason_codes[0],
                    )

            if existing_member is None and decision.outcome == "CREATE_EVENT":
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
                repository.flush()
                save_current_event_profile(repository, group, now=now)
                schedule_event_lifecycle(repository, group, now=now)
                action = "CREATE_GROUP"
            elif existing_member is None:
                if decision.selected_group_id is None:
                    raise RuntimeError("alert_grouping_selected_group_missing")
                selected_group = repository.find_group(decision.selected_group_id, for_update=True)
                if selected_group is None:
                    raise RuntimeError("alert_grouping_candidate_not_found")
                if selected_group.total_count >= selected_group.member_limit:
                    decision = MembershipDecision(
                        outcome="CREATE_EVENT",
                        selected_group_id=None,
                        candidate_group_ids=decision.candidate_group_ids,
                        reason_codes=("event_member_limit_reached",),
                        explanation="候选事件已达到成员上限, 已创建同一风暴的后继事件。",
                    )
                    group = _new_group(
                        group_id=self._id_factory("agr"),
                        alert=alert,
                        signature=signature,
                        symptom=symptom,
                        reason_code=decision.reason_codes[0],
                        explanation=decision.explanation,
                        incident_id=(
                            selected_group.incident_id
                            if existing_link is None
                            else existing_link.incident_id
                        ),
                        continuation_group_id=selected_group.id,
                        now=now,
                    )
                    repository.add_group(group)
                    repository.add_member(
                        _new_member(group.id, alert, identity, decision.reason_codes[0], now)
                    )
                    repository.flush()
                    save_current_event_profile(repository, group, now=now)
                    schedule_event_lifecycle(repository, group, now=now)
                    action = "CREATE_GROUP"
                else:
                    group = selected_group
                    if existing_link is not None and group.incident_id is None:
                        group.incident_id = existing_link.incident_id
                    member = _new_member(group.id, alert, identity, decision.reason_codes[0], now)
                    repository.add_member(member)
                    late_correction = (
                        group.state == "CLOSED"
                        and group.closed_at is not None
                        and signal.observed_at <= group.closed_at
                        and signal.received_at <= group.closed_at + timedelta(minutes=5)
                    )
                    refresh_group_membership(
                        repository,
                        group,
                        reason_codes=list(decision.reason_codes),
                        explanation=decision.explanation,
                        now=now,
                        allow_late_correction=late_correction,
                    )
                    group.profile_version += 1
                    save_current_event_profile(repository, group, now=now)
                    schedule_event_lifecycle(repository, group, now=now)
                    action = "JOIN_GROUP"

            repository.save_membership_decision(
                decision_id=self._id_factory("amd"),
                alert=alert,
                state="AUTO_CONFIRMED",
                decision=decision,
                scores=scores,
                selected_group_id=group.id,
                selected_group_version=group.version,
                created_at=now,
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
                action=action,
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
                action=action,
                reason_code=decision.reason_codes[0],
            )

    def _score_candidate(
        self,
        *,
        alert: AlertRow,
        signal: SignalEventRow,
        symptom: str,
        signature: ProblemSignature,
        identity: ResourceIdentity,
        candidate: AlertEventCandidateRecord,
        repository: AlertGroupRepository,
    ) -> CandidateScore:
        member_facts = repository.event_member_facts(candidate.group.id)
        profile = _build_profile(candidate.group, member_facts)
        incoming_text = normalize_alert_text(
            alert.title,
            signal.summary,
            signal.facts.get("alertname") or alert.title,
            symptom,
        )
        matched_member_ids = tuple(
            fact.alert.id
            for fact in member_facts
            if fact.alert.entity_key == alert.entity_key
            or (alert.service is not None and fact.alert.service == alert.service)
            or _member_problem_key(fact) == signature.problem_key
            or (
                candidate.topology_distance is not None
                and fact.alert.service == candidate.group.service
            )
        )[:50]
        return score_candidate(
            CandidateContext(
                alert_id=alert.id,
                environment=alert.environment,
                service=alert.service,
                entity_key=alert.entity_key,
                scope_type=signature.scope_type,
                problem_key=signature.problem_key,
                problem_type=signature.problem_type,
                symptom=symptom,
                observed_at=alert.last_observed_at,
                topology_distance=candidate.topology_distance,
                matched_member_ids=matched_member_ids,
                text_similarity=self._text_similarity.compare(
                    incoming_text, profile.normalized_text
                ),
                history_signal="NONE",
            ),
            profile,
        )


def _build_profile(
    group: AlertGroupRow,
    member_facts: tuple[AlertEventMemberFact, ...],
    *,
    manual_alert_ids: frozenset[str] = frozenset(),
) -> AlertEventProfile:
    members = tuple(
        _confirmed_member(
            fact,
            membership_state=(
                "MANUAL_CONFIRMED" if fact.alert.id in manual_alert_ids else "AUTO_CONFIRMED"
            ),
        )
        for fact in member_facts
    )
    return build_event_profile(
        group.id,
        members,
        profile_version=group.profile_version,
    )


def _late_membership_decision(
    *,
    alert: AlertRow,
    signal: SignalEventRow,
    signature: ProblemSignature,
    candidates: tuple[AlertEventCandidateRecord, ...],
    truncated: bool,
) -> MembershipDecision | None:
    if truncated:
        return None
    eligible = tuple(
        candidate.group
        for candidate in candidates
        if candidate.group.state == "CLOSED"
        and candidate.group.closed_at is not None
        and candidate.group.environment == alert.environment
        and candidate.group.service == alert.service
        and candidate.group.problem_key == signature.problem_key
        and signal.observed_at <= candidate.group.closed_at
        and signal.received_at <= candidate.group.closed_at + timedelta(minutes=5)
    )
    if len(eligible) != 1:
        return None
    group = eligible[0]
    return MembershipDecision(
        outcome="AUTO_JOIN",
        selected_group_id=group.id,
        candidate_group_ids=(group.id,),
        reason_codes=("late_alert_matches_recent_event",),
        explanation="迟到告警与刚关闭事件的环境、服务和问题签名完全一致, 已补入原事件。",
    )


def _confirmed_member(
    fact: AlertEventMemberFact,
    *,
    membership_state: Literal["AUTO_CONFIRMED", "MANUAL_CONFIRMED"] = "AUTO_CONFIRMED",
) -> ConfirmedEventMember:
    signature = _member_signature(fact)
    symptom = normalize_symptom(fact.signal.facts.get("symptom")) or "unknown"
    return ConfirmedEventMember(
        alert_id=fact.alert.id,
        membership_state=membership_state,
        environment=fact.alert.environment,
        service=fact.alert.service,
        entity_key=fact.alert.entity_key,
        scope_type=signature.scope_type,
        problem_key=signature.problem_key,
        problem_type=signature.problem_type,
        symptom=symptom,
        topology_nodes=(() if fact.alert.service is None else (fact.alert.service,)),
        normalized_text=normalize_alert_text(
            fact.alert.title,
            fact.signal.summary,
            fact.signal.facts.get("alertname") or fact.alert.title,
            symptom,
        ),
        observed_at=fact.alert.last_observed_at,
    )


def _member_problem_key(fact: AlertEventMemberFact) -> str:
    return _member_signature(fact).problem_key


def _member_signature(fact: AlertEventMemberFact) -> ProblemSignature:
    symptom = normalize_symptom(fact.signal.facts.get("symptom")) or "unknown"
    signature_facts = dict(fact.signal.facts)
    if fact.alert.service is not None:
        signature_facts["service"] = fact.alert.service
    return derive_problem_signature(
        alert_source_id=fact.alert.alert_source_id,
        problem_type=fact.signal.facts.get("alertname") or fact.alert.title,
        symptom=symptom,
        environment=fact.alert.environment,
        facts=signature_facts,
    )


def save_current_event_profile(
    repository: AlertGroupRepository,
    group: AlertGroupRow,
    *,
    now: datetime,
) -> AlertEventProfile:
    repository.flush()
    profile = _build_profile(
        group,
        repository.event_member_facts(group.id),
        manual_alert_ids=repository.manual_member_alert_ids(group.id),
    )
    repository.save_profile(profile, pending_count=group.pending_count, created_at=now)
    return profile


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
    continuation_group_id: str | None = None,
) -> AlertGroupRow:
    active_count = 1 if alert.state == "ACTIVE" else 0
    return AlertGroupRow(
        id=group_id,
        state="FORMING",
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
        forming_until=now + timedelta(seconds=30),
        observing_until=None,
        closed_at=None,
        member_limit=1_000,
        continuation_group_id=continuation_group_id,
        pending_count=0,
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


def refresh_group_membership(
    repository: AlertGroupRepository,
    group: AlertGroupRow,
    *,
    reason_codes: list[str],
    explanation: str,
    now: datetime,
    allow_late_correction: bool = False,
) -> None:
    aggregate = repository.aggregate_group(
        group.id,
        recent_since=now - timedelta(seconds=STORM_WINDOW_SECONDS),
    )
    if aggregate is None:
        raise RuntimeError("alert_grouping_group_has_no_members")

    previous_state = group.state
    lifecycle = decide_lifecycle(
        LifecycleContext(
            state=cast(AlertEventState, group.state),
            active_count=aggregate.active_count,
            now=now,
            forming_until=group.forming_until,
            observing_until=group.observing_until,
            closed_at=group.closed_at,
            allow_late_correction=allow_late_correction,
        )
    )
    group.state = lifecycle.state
    group.forming_until = lifecycle.forming_until
    group.observing_until = lifecycle.observing_until
    group.closed_at = lifecycle.closed_at
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


def schedule_event_lifecycle(
    repository: AlertGroupRepository,
    group: AlertGroupRow,
    *,
    now: datetime,
) -> None:
    if group.state == "FORMING" and group.forming_until is not None:
        repository.schedule_lifecycle(
            group,
            action="FORMING_COMPLETE",
            available_at=group.forming_until,
            now=now,
        )
    elif group.state == "OBSERVING" and group.observing_until is not None:
        repository.schedule_lifecycle(
            group,
            action="OBSERVATION_COMPLETE",
            available_at=group.observing_until,
            now=now,
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


def _append_pending_audit(
    records: RecordRepositories,
    *,
    audit_id: str,
    alert: AlertRow,
    reason_code: str,
    now: datetime,
) -> None:
    records.add_audit(
        audit_id=audit_id,
        actor="alert-grouping-worker",
        action="alert.grouping_pending",
        resource_type="alert",
        resource_id=alert.id,
        request_id=alert.id,
        details={
            "reason_code": reason_code,
            "rule_version": GROUPING_RULE_VERSION,
            "alert_id": alert.id,
        },
        created_at=now,
    )


def _requires_correlation(group: AlertGroupRow, catalog_entry: object | None) -> bool:
    if group.incident_id is not None:
        return True
    base_eligible = (
        group.state in {"FORMING", "ACTIVE"}
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
