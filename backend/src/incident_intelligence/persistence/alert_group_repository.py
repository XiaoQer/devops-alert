from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from sqlalchemy import and_, case, exists, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from incident_intelligence.domain.alert_event_clustering import (
    CandidateScore,
    MembershipDecision,
)
from incident_intelligence.domain.alert_event_profiles import AlertEventProfile
from incident_intelligence.ids import new_id
from incident_intelligence.persistence.models import (
    AlertEventLifecycleJobRow,
    AlertEventMembershipDecisionRow,
    AlertEventProfileRow,
    AlertGroupCorrelationJobRow,
    AlertGroupDecisionRow,
    AlertGroupingJobRow,
    AlertGroupMemberRow,
    AlertGroupRow,
    AlertRow,
    CorrelationJobRow,
    IncidentAlertLinkRow,
    IncidentRow,
    ServiceCatalogEntryRow,
    ServiceDependencyRow,
    SignalEventRow,
)


@dataclass(frozen=True, slots=True)
class AlertGroupAggregate:
    total_count: int
    active_count: int
    impacted_resource_count: int
    first_observed_at: datetime
    last_observed_at: datetime
    last_member_at: datetime
    recent_member_count: int
    representative_alert_id: str
    representative_title: str
    representative_severity: str


@dataclass(frozen=True, slots=True)
class AlertEventCandidateRecord:
    group: AlertGroupRow
    topology_distance: int | None


@dataclass(frozen=True, slots=True)
class AlertEventCandidateBatch:
    items: tuple[AlertEventCandidateRecord, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class AlertEventMemberFact:
    member: AlertGroupMemberRow
    alert: AlertRow
    signal: SignalEventRow


class AlertGroupRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue_grouping(
        self,
        *,
        alert_id: str,
        alert_cycle: int,
        alert_version: int,
        now: datetime,
    ) -> AlertGroupingJobRow:
        row = AlertGroupingJobRow(
            id=new_id("agj"),
            alert_id=alert_id,
            alert_cycle=alert_cycle,
            alert_version=alert_version,
            state="PENDING",
            attempts=0,
            available_at=now,
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=None,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def backfill_candidates(self, *, limit: int) -> tuple[AlertRow, ...]:
        has_current_member = exists(
            select(AlertGroupMemberRow.alert_id).where(
                AlertGroupMemberRow.alert_id == AlertRow.id,
                AlertGroupMemberRow.alert_cycle == AlertRow.cycle,
            )
        )
        has_active_legacy_job = exists(
            select(CorrelationJobRow.id).where(
                CorrelationJobRow.alert_id == AlertRow.id,
                CorrelationJobRow.state.in_(("PENDING", "LEASED")),
            )
        )
        has_current_grouping_job = exists(
            select(AlertGroupingJobRow.id).where(
                AlertGroupingJobRow.alert_id == AlertRow.id,
                AlertGroupingJobRow.alert_version == AlertRow.version,
            )
        )
        return tuple(
            self._session.scalars(
                select(AlertRow)
                .where(
                    ~has_current_member,
                    ~has_active_legacy_job,
                    ~has_current_grouping_job,
                )
                .order_by(AlertRow.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )

    def claimable_grouping_jobs(
        self, *, now: datetime, limit: int
    ) -> tuple[AlertGroupingJobRow, ...]:
        statement = (
            select(AlertGroupingJobRow)
            .where(
                or_(
                    and_(
                        AlertGroupingJobRow.state == "PENDING",
                        AlertGroupingJobRow.available_at <= now,
                    ),
                    and_(
                        AlertGroupingJobRow.state == "LEASED",
                        AlertGroupingJobRow.lease_expires_at <= now,
                    ),
                )
            )
            .order_by(
                AlertGroupingJobRow.available_at,
                AlertGroupingJobRow.created_at,
                AlertGroupingJobRow.id,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return tuple(self._session.scalars(statement))

    def find_grouping_job(
        self, job_id: str, *, for_update: bool = False
    ) -> AlertGroupingJobRow | None:
        statement = select(AlertGroupingJobRow).where(AlertGroupingJobRow.id == job_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def find_lifecycle_job(
        self, job_id: str, *, for_update: bool = False
    ) -> AlertEventLifecycleJobRow | None:
        statement = select(AlertEventLifecycleJobRow).where(AlertEventLifecycleJobRow.id == job_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def find_active_lifecycle_job(
        self,
        group_id: str,
        action: str,
        *,
        for_update: bool = False,
    ) -> AlertEventLifecycleJobRow | None:
        statement = select(AlertEventLifecycleJobRow).where(
            AlertEventLifecycleJobRow.alert_group_id == group_id,
            AlertEventLifecycleJobRow.action == action,
            AlertEventLifecycleJobRow.active_slot == 1,
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def schedule_lifecycle(
        self,
        group: AlertGroupRow,
        *,
        action: str,
        available_at: datetime,
        now: datetime,
    ) -> AlertEventLifecycleJobRow:
        active = self.find_active_lifecycle_job(group.id, action, for_update=True)
        if active is not None:
            if active.state == "PENDING":
                active.target_group_version = max(active.target_group_version, group.version)
                active.available_at = available_at
                active.updated_at = now
            self._session.flush()
            return active
        row = AlertEventLifecycleJobRow(
            id=new_id("alj"),
            alert_group_id=group.id,
            target_group_version=group.version,
            action=action,
            state="PENDING",
            active_slot=1,
            attempts=0,
            available_at=available_at,
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=None,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def claimable_lifecycle_jobs(
        self, *, now: datetime, limit: int
    ) -> tuple[AlertEventLifecycleJobRow, ...]:
        statement = (
            select(AlertEventLifecycleJobRow)
            .where(
                or_(
                    and_(
                        AlertEventLifecycleJobRow.state == "PENDING",
                        AlertEventLifecycleJobRow.available_at <= now,
                    ),
                    and_(
                        AlertEventLifecycleJobRow.state == "LEASED",
                        AlertEventLifecycleJobRow.lease_expires_at <= now,
                    ),
                )
            )
            .order_by(
                AlertEventLifecycleJobRow.available_at,
                AlertEventLifecycleJobRow.created_at,
                AlertEventLifecycleJobRow.id,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return tuple(self._session.scalars(statement))

    def find_alert_for_update(self, alert_id: str) -> AlertRow | None:
        return self._session.scalar(
            select(AlertRow).where(AlertRow.id == alert_id).with_for_update()
        )

    def find_signal(self, signal_event_id: str) -> SignalEventRow | None:
        return self._session.get(SignalEventRow, signal_event_id)

    def find_member(
        self, alert_id: str, alert_cycle: int, *, for_update: bool = False
    ) -> AlertGroupMemberRow | None:
        statement = select(AlertGroupMemberRow).where(
            AlertGroupMemberRow.alert_id == alert_id,
            AlertGroupMemberRow.alert_cycle == alert_cycle,
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def find_group(self, group_id: str, *, for_update: bool = False) -> AlertGroupRow | None:
        statement = select(AlertGroupRow).where(AlertGroupRow.id == group_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def active_candidates(
        self,
        *,
        problem_key: str,
        limit: int,
    ) -> tuple[AlertGroupRow, ...]:
        return tuple(
            self._session.scalars(
                select(AlertGroupRow)
                .where(
                    AlertGroupRow.state == "ACTIVE",
                    AlertGroupRow.problem_key == problem_key,
                )
                .order_by(AlertGroupRow.last_observed_at.desc(), AlertGroupRow.id)
                .limit(limit)
                .with_for_update()
            )
        )

    def candidate_events(
        self,
        *,
        environment: str,
        observed_at: datetime,
        received_at: datetime,
        entity_key: str,
        service: str | None,
        problem_key: str,
        alert_source_id: str | None = None,
        limit: int = 50,
    ) -> AlertEventCandidateBatch:
        if not 1 <= limit <= 50:
            raise ValueError("alert_event_candidate_limit_out_of_range")
        topology_distances = self._topology_distances(service, environment)
        anchors = [
            AlertGroupRow.entity_key == entity_key,
            AlertGroupRow.problem_key == problem_key,
        ]
        if service is not None:
            anchors.append(AlertGroupRow.service == service)
        if topology_distances:
            anchors.append(AlertGroupRow.service.in_(tuple(topology_distances)))
        safety_filters = []
        if environment == "unknown" and alert_source_id is not None:
            safety_filters.append(
                exists(
                    select(AlertGroupMemberRow.alert_id)
                    .join(AlertRow, AlertRow.id == AlertGroupMemberRow.alert_id)
                    .where(
                        AlertGroupMemberRow.alert_group_id == AlertGroupRow.id,
                        AlertRow.alert_source_id == alert_source_id,
                    )
                )
            )
        rows = tuple(
            self._session.scalars(
                select(AlertGroupRow)
                .where(
                    or_(
                        and_(
                            AlertGroupRow.state.in_(("FORMING", "ACTIVE", "OBSERVING")),
                            AlertGroupRow.last_observed_at >= observed_at - timedelta(minutes=15),
                            AlertGroupRow.last_observed_at <= observed_at + timedelta(minutes=5),
                        ),
                        and_(
                            AlertGroupRow.state == "CLOSED",
                            AlertGroupRow.closed_at.is_not(None),
                            AlertGroupRow.closed_at >= observed_at,
                            AlertGroupRow.closed_at >= received_at - timedelta(minutes=5),
                            AlertGroupRow.first_observed_at <= observed_at + timedelta(minutes=5),
                        ),
                    ),
                    AlertGroupRow.environment == environment,
                    or_(*anchors),
                    *safety_filters,
                )
                .order_by(AlertGroupRow.last_observed_at.desc(), AlertGroupRow.id)
                .limit(limit + 1)
                .with_for_update()
            )
        )
        return AlertEventCandidateBatch(
            items=tuple(
                AlertEventCandidateRecord(
                    group=row,
                    topology_distance=(
                        None if row.service is None else topology_distances.get(row.service)
                    ),
                )
                for row in rows[:limit]
            ),
            truncated=len(rows) > limit,
        )

    def event_member_facts(self, group_id: str) -> tuple[AlertEventMemberFact, ...]:
        rows = self._session.execute(
            select(AlertGroupMemberRow, AlertRow, SignalEventRow)
            .join(AlertRow, AlertRow.id == AlertGroupMemberRow.alert_id)
            .join(SignalEventRow, SignalEventRow.id == AlertRow.signal_event_id)
            .where(AlertGroupMemberRow.alert_group_id == group_id)
            .order_by(AlertGroupMemberRow.alert_id, AlertGroupMemberRow.alert_cycle)
            .limit(1_000)
        )
        return tuple(
            AlertEventMemberFact(member=member, alert=alert, signal=signal)
            for member, alert, signal in rows
        )

    def manual_member_alert_ids(self, group_id: str) -> frozenset[str]:
        current_member = exists(
            select(AlertGroupMemberRow.alert_id).where(
                AlertGroupMemberRow.alert_group_id == group_id,
                AlertGroupMemberRow.alert_id == AlertEventMembershipDecisionRow.alert_id,
                AlertGroupMemberRow.alert_cycle == AlertEventMembershipDecisionRow.alert_cycle,
            )
        )
        return frozenset(
            self._session.scalars(
                select(AlertEventMembershipDecisionRow.alert_id)
                .where(
                    AlertEventMembershipDecisionRow.selected_group_id == group_id,
                    AlertEventMembershipDecisionRow.state == "MANUAL_CONFIRMED",
                    current_member,
                )
                .distinct()
                .limit(1_000)
            )
        )

    def save_profile(
        self,
        profile: AlertEventProfile,
        *,
        pending_count: int,
        created_at: datetime,
    ) -> AlertEventProfileRow:
        row = AlertEventProfileRow(
            alert_group_id=profile.group_id,
            profile_version=profile.profile_version,
            environment=profile.environment,
            services=list(profile.services),
            entity_keys=list(profile.entity_keys),
            scope_types=list(profile.scope_types),
            topology_edges=self._profile_topology_edges(profile),
            problem_types=list(profile.problem_types),
            symptoms=list(profile.symptoms),
            normalized_text=profile.normalized_text.normalized,
            first_observed_at=profile.first_observed_at,
            last_observed_at=profile.last_observed_at,
            auto_confirmed_count=profile.auto_confirmed_count,
            manual_confirmed_count=profile.manual_confirmed_count,
            pending_count=pending_count,
            rule_version=profile.rule_version,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def _profile_topology_edges(self, profile: AlertEventProfile) -> list[dict[str, str]]:
        if len(profile.services) < 2:
            return []
        entries = tuple(
            self._session.scalars(
                select(ServiceCatalogEntryRow).where(
                    ServiceCatalogEntryRow.environment == profile.environment,
                    ServiceCatalogEntryRow.state == "ACTIVE",
                    ServiceCatalogEntryRow.service.in_(profile.services),
                )
            )
        )
        names = {row.id: row.service for row in entries}
        if len(names) < 2:
            return []
        edges = self._session.scalars(
            select(ServiceDependencyRow)
            .where(
                ServiceDependencyRow.state == "ACTIVE",
                ServiceDependencyRow.caller_service_id.in_(tuple(names)),
                ServiceDependencyRow.dependency_service_id.in_(tuple(names)),
            )
            .order_by(
                ServiceDependencyRow.caller_service_id,
                ServiceDependencyRow.dependency_service_id,
                ServiceDependencyRow.id,
            )
            .limit(2_000)
        )
        return [
            {
                "caller": names[row.caller_service_id],
                "dependency": names[row.dependency_service_id],
            }
            for row in edges
        ]

    def save_membership_decision(
        self,
        *,
        decision_id: str,
        alert: AlertRow,
        state: str,
        decision: MembershipDecision,
        scores: tuple[CandidateScore, ...],
        selected_group_id: str | None,
        selected_group_version: int | None,
        created_at: datetime,
    ) -> AlertEventMembershipDecisionRow:
        row = AlertEventMembershipDecisionRow(
            id=decision_id,
            alert_id=alert.id,
            alert_cycle=alert.cycle,
            alert_version=alert.version,
            state=state,
            candidate_group_ids=list(decision.candidate_group_ids),
            selected_group_id=selected_group_id,
            selected_group_version=selected_group_version,
            rule_version="alert-event-clustering.v1",
            scores={item.group_id: item.model_dump(mode="json") for item in scores},
            reason_codes=list(decision.reason_codes),
            explanation=decision.explanation,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def _topology_distances(self, service: str | None, environment: str) -> dict[str, int]:
        if service is None:
            return {}
        root = self._session.scalar(
            select(ServiceCatalogEntryRow).where(
                ServiceCatalogEntryRow.service == service,
                ServiceCatalogEntryRow.environment == environment,
                ServiceCatalogEntryRow.state == "ACTIVE",
            )
        )
        if root is None:
            return {}
        direct_edges = tuple(
            self._session.scalars(
                select(ServiceDependencyRow)
                .where(
                    ServiceDependencyRow.state == "ACTIVE",
                    or_(
                        ServiceDependencyRow.caller_service_id == root.id,
                        ServiceDependencyRow.dependency_service_id == root.id,
                    ),
                )
                .order_by(ServiceDependencyRow.id)
                .limit(100)
            )
        )
        direct_ids = {
            edge.dependency_service_id
            if edge.caller_service_id == root.id
            else edge.caller_service_id
            for edge in direct_edges
        }
        two_hop_ids: set[str] = set()
        if direct_ids:
            second_edges = tuple(
                self._session.scalars(
                    select(ServiceDependencyRow)
                    .where(
                        ServiceDependencyRow.state == "ACTIVE",
                        or_(
                            ServiceDependencyRow.caller_service_id.in_(direct_ids),
                            ServiceDependencyRow.dependency_service_id.in_(direct_ids),
                        ),
                    )
                    .order_by(ServiceDependencyRow.id)
                    .limit(500)
                )
            )
            for edge in second_edges:
                two_hop_ids.add(edge.caller_service_id)
                two_hop_ids.add(edge.dependency_service_id)
            two_hop_ids.difference_update(direct_ids | {root.id})
        distance_by_id = {item: 1 for item in direct_ids}
        distance_by_id.update({item: 2 for item in two_hop_ids})
        if not distance_by_id:
            return {}
        entries = self._session.scalars(
            select(ServiceCatalogEntryRow).where(
                ServiceCatalogEntryRow.id.in_(tuple(distance_by_id)),
                ServiceCatalogEntryRow.environment == environment,
                ServiceCatalogEntryRow.state == "ACTIVE",
            )
        )
        return {row.service: distance_by_id[row.id] for row in entries}

    def legacy_regroup_candidates(self, *, limit: int) -> tuple[AlertGroupRow, ...]:
        return tuple(
            self._session.scalars(
                select(AlertGroupRow)
                .where(
                    AlertGroupRow.state == "ACTIVE",
                    AlertGroupRow.service.is_(None),
                    AlertGroupRow.incident_id.is_(None),
                    AlertGroupRow.rule_version != "alert-event-clustering.v1",
                )
                .order_by(AlertGroupRow.problem_key, AlertGroupRow.created_at, AlertGroupRow.id)
                .limit(limit)
                .with_for_update()
            )
        )

    def move_members(self, *, source_group_id: str, target_group_id: str, now: datetime) -> int:
        result = cast(
            CursorResult[object],
            self._session.execute(
                update(AlertGroupMemberRow)
                .where(AlertGroupMemberRow.alert_group_id == source_group_id)
                .values(
                    alert_group_id=target_group_id,
                    reason_code="regrouped_into_problem_signature",
                    updated_at=now,
                )
            ),
        )
        self._session.flush()
        return int(result.rowcount or 0)

    def find_incident_link(self, alert_id: str) -> IncidentAlertLinkRow | None:
        return self._session.scalar(
            select(IncidentAlertLinkRow).where(IncidentAlertLinkRow.alert_id == alert_id)
        )

    def add_group(self, row: AlertGroupRow) -> None:
        self._session.add(row)
        self._session.flush()

    def add_member(self, row: AlertGroupMemberRow) -> None:
        self._session.add(row)
        self._session.flush()

    def list_members(self, group_id: str) -> tuple[AlertGroupMemberRow, ...]:
        return tuple(
            self._session.scalars(
                select(AlertGroupMemberRow).where(AlertGroupMemberRow.alert_group_id == group_id)
            )
        )

    def aggregate_group(
        self, group_id: str, *, recent_since: datetime
    ) -> AlertGroupAggregate | None:
        row = self._session.execute(
            select(
                func.count(AlertGroupMemberRow.alert_id),
                func.sum(case((AlertGroupMemberRow.current_state == "ACTIVE", 1), else_=0)),
                func.count(func.distinct(AlertGroupMemberRow.resource_key)),
                func.min(AlertRow.first_observed_at),
                func.max(AlertRow.last_observed_at),
                func.max(AlertGroupMemberRow.joined_at),
                func.sum(case((AlertGroupMemberRow.joined_at >= recent_since, 1), else_=0)),
            )
            .join(AlertRow, AlertRow.id == AlertGroupMemberRow.alert_id)
            .where(AlertGroupMemberRow.alert_group_id == group_id)
        ).one()
        if not row[0]:
            return None
        representative = self._session.execute(
            select(
                AlertGroupMemberRow.alert_id,
                AlertRow.title,
                AlertGroupMemberRow.current_severity,
            )
            .join(AlertRow, AlertRow.id == AlertGroupMemberRow.alert_id)
            .where(AlertGroupMemberRow.alert_group_id == group_id)
            .order_by(
                case(
                    (AlertGroupMemberRow.current_severity == "critical", 3),
                    (AlertGroupMemberRow.current_severity == "high", 2),
                    (AlertGroupMemberRow.current_severity == "medium", 1),
                    else_=0,
                ).desc(),
                AlertRow.last_observed_at.desc(),
                AlertGroupMemberRow.alert_id.desc(),
            )
            .limit(1)
        ).one()
        return AlertGroupAggregate(
            total_count=int(row[0]),
            active_count=int(row[1] or 0),
            impacted_resource_count=int(row[2]),
            first_observed_at=cast(datetime, row[3]),
            last_observed_at=cast(datetime, row[4]),
            last_member_at=cast(datetime, row[5]),
            recent_member_count=int(row[6] or 0),
            representative_alert_id=cast(str, representative[0]),
            representative_title=cast(str, representative[1]),
            representative_severity=cast(str, representative[2]),
        )

    def find_alert(self, alert_id: str) -> AlertRow | None:
        return self._session.get(AlertRow, alert_id)

    def find_active_correlation_job(
        self, group_id: str, *, for_update: bool = False
    ) -> AlertGroupCorrelationJobRow | None:
        statement = select(AlertGroupCorrelationJobRow).where(
            AlertGroupCorrelationJobRow.alert_group_id == group_id,
            AlertGroupCorrelationJobRow.active_slot == 1,
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def schedule_correlation(
        self, group: AlertGroupRow, *, target_version: int, now: datetime
    ) -> AlertGroupCorrelationJobRow | None:
        group.desired_correlation_version = max(group.desired_correlation_version, target_version)
        active = self.find_active_correlation_job(group.id, for_update=True)
        if active is not None:
            if active.state == "PENDING":
                active.target_group_version = max(active.target_group_version, target_version)
                active.available_at = min(active.available_at, now)
                active.updated_at = now
            self._session.flush()
            return active
        row = AlertGroupCorrelationJobRow(
            id=new_id("gcj"),
            alert_group_id=group.id,
            target_group_version=target_version,
            state="PENDING",
            active_slot=1,
            attempts=0,
            available_at=now,
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=None,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def claimable_correlation_jobs(
        self, *, now: datetime, limit: int
    ) -> tuple[AlertGroupCorrelationJobRow, ...]:
        statement = (
            select(AlertGroupCorrelationJobRow)
            .where(
                or_(
                    and_(
                        AlertGroupCorrelationJobRow.state == "PENDING",
                        AlertGroupCorrelationJobRow.available_at <= now,
                    ),
                    and_(
                        AlertGroupCorrelationJobRow.state == "LEASED",
                        AlertGroupCorrelationJobRow.lease_expires_at <= now,
                    ),
                )
            )
            .order_by(
                AlertGroupCorrelationJobRow.available_at,
                AlertGroupCorrelationJobRow.created_at,
                AlertGroupCorrelationJobRow.id,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return tuple(self._session.scalars(statement))

    def find_correlation_job(
        self, job_id: str, *, for_update: bool = False
    ) -> AlertGroupCorrelationJobRow | None:
        statement = select(AlertGroupCorrelationJobRow).where(
            AlertGroupCorrelationJobRow.id == job_id
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def add_group_decision(self, row: AlertGroupDecisionRow) -> None:
        self._session.add(row)
        self._session.flush()

    def add_incident(self, row: IncidentRow) -> None:
        self._session.add(row)
        self._session.flush()

    def add_incident_link(self, row: IncidentAlertLinkRow) -> None:
        self._session.add(row)
        self._session.flush()

    def flush(self) -> None:
        self._session.flush()
