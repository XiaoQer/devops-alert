from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial
from threading import Barrier

from sqlalchemy import event, func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.signal_intake import SignalCommand
from incident_intelligence.ids import new_id
from incident_intelligence.persistence.models import (
    AlertEventMembershipDecisionRow,
    AlertEventProfileRow,
    AlertGroupingJobRow,
    AlertGroupMemberRow,
    AlertGroupRow,
    AlertRow,
    AlertSourceRow,
    AuditEventRow,
    CorrelationJobRow,
    ServiceCatalogEntryRow,
    ServiceDependencyRow,
    SignalEventRow,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_grouping import AlertGroupingService
from incident_intelligence.services.alert_grouping_jobs import AlertGroupingJobService
from incident_intelligence.services.signal_intake import SignalIntakeService

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)
ALERT_SOURCE_ID = "src_00000000000000000000000000000002"


def command(number: int, **overrides: object) -> SignalCommand:
    values: dict[str, object] = {
        "alert_source_id": ALERT_SOURCE_ID,
        "source": "alertmanager",
        "source_instance": f"{number:064x}",
        "source_event_id": f"{number:064x}",
        "source_alert_key": f"payment-error-{number}",
        "event_type": "alert.firing",
        "event_at": NOW + timedelta(seconds=number),
        "episode_started_at": NOW + timedelta(seconds=number),
        "title": "支付接口错误率升高",
        "summary": "支付接口错误率超过阈值",
        "severity": "high",
        "service": "payment-api",
        "environment": "production",
        "facts": {"symptom": "errors", "pod": f"payment-{number}"},
    }
    values.update(overrides)
    return SignalCommand.model_validate(values)


def services(
    session_factory: sessionmaker[Session], clock: list[datetime]
) -> tuple[SignalIntakeService, AlertGroupingJobService, AlertGroupingService]:
    uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
    return (
        SignalIntakeService(uow_factory=uow_factory, clock=lambda: clock[0]),
        AlertGroupingJobService(uow_factory=uow_factory),
        AlertGroupingService(uow_factory=uow_factory, clock=lambda: clock[0]),
    )


def seed_catalog(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        session.add(
            ServiceCatalogEntryRow(
                id=new_id("svc"),
                service="payment-api",
                environment="production",
                owner_team="payments",
                state="ACTIVE",
                created_at=NOW,
                updated_at=NOW,
                version=1,
            )
        )
        session.commit()


def seed_topology_and_source(session_factory: sessionmaker[Session]) -> str:
    secondary_source_id = "src_00000000000000000000000000000004"
    with session_factory.begin() as session:
        database = ServiceCatalogEntryRow(
            id=new_id("svc"),
            service="business-mysql",
            environment="production",
            owner_team="database",
            state="ACTIVE",
            created_at=NOW,
            updated_at=NOW,
            version=1,
        )
        payment = ServiceCatalogEntryRow(
            id=new_id("svc"),
            service="payment-api",
            environment="production",
            owner_team="payments",
            state="ACTIVE",
            created_at=NOW,
            updated_at=NOW,
            version=1,
        )
        session.add_all((database, payment))
        session.flush()
        session.add(
            ServiceDependencyRow(
                id=new_id("dep"),
                caller_service_id=payment.id,
                dependency_service_id=database.id,
                state="ACTIVE",
                created_at=NOW,
                updated_at=NOW,
                version=1,
            )
        )
        session.add(
            AlertSourceRow(
                id=secondary_source_id,
                name="第二套生产监控",
                source_type="ALERTMANAGER",
                management_type="USER_MANAGED",
                state="ENABLED",
                environment="production",
                environment_name="生产环境",
                environment_configured=True,
                version=1,
                last_accepted_at=None,
                last_rejected_at=None,
                last_validated_at=None,
                accepted_requests=0,
                rejected_requests=0,
                opened_count=0,
                updated_count=0,
                resolved_count=0,
                replayed_count=0,
                ignored_count=0,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    return secondary_source_id


def seed_source(
    session_factory: sessionmaker[Session],
    *,
    source_id: str,
    name: str,
    environment: str,
    environment_configured: bool = True,
) -> None:
    with session_factory.begin() as session:
        session.add(
            AlertSourceRow(
                id=source_id,
                name=name,
                source_type="ALERTMANAGER",
                management_type="USER_MANAGED",
                state="ENABLED",
                environment=environment,
                environment_name=environment,
                environment_configured=environment_configured,
                version=1,
                last_accepted_at=None,
                last_rejected_at=None,
                last_validated_at=None,
                accepted_requests=0,
                rejected_requests=0,
                opened_count=0,
                updated_count=0,
                resolved_count=0,
                replayed_count=0,
                ignored_count=0,
                created_at=NOW,
                updated_at=NOW,
            )
        )


def seed_ambiguous_topology(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        services = {
            name: ServiceCatalogEntryRow(
                id=new_id("svc"),
                service=name,
                environment="production",
                owner_team="platform",
                state="ACTIVE",
                created_at=NOW,
                updated_at=NOW,
                version=1,
            )
            for name in ("payment-api", "business-mysql", "business-redis")
        }
        session.add_all(tuple(services.values()))
        session.flush()
        session.add_all(
            (
                ServiceDependencyRow(
                    id=new_id("dep"),
                    caller_service_id=services["payment-api"].id,
                    dependency_service_id=services["business-mysql"].id,
                    state="ACTIVE",
                    created_at=NOW,
                    updated_at=NOW,
                    version=1,
                ),
                ServiceDependencyRow(
                    id=new_id("dep"),
                    caller_service_id=services["payment-api"].id,
                    dependency_service_id=services["business-redis"].id,
                    state="ACTIVE",
                    created_at=NOW,
                    updated_at=NOW,
                    version=1,
                ),
            )
        )


def test_direct_dependency_alerts_from_different_sources_join_one_event(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    secondary_source_id = seed_topology_and_source(session_factory)
    clock = [NOW + timedelta(minutes=1)]
    intake, jobs, grouping = services(session_factory, clock)

    intake.submit_batch(
        [
            command(
                101,
                service="business-mysql",
                title="MySQL 行锁等待持续升高",
                summary="业务库存在活动锁等待",
                facts={"alertname": "MySQLRowLockWaitActive", "symptom": "lock"},
            )
        ],
        "alertmanager-adapter",
        "req-database",
    )
    first = grouping.process(
        jobs.claim_batch("grouping-runner", clock[0], limit=1, lease_seconds=30)[0]
    )
    intake.submit_batch(
        [
            command(
                102,
                alert_source_id=secondary_source_id,
                service="payment-api",
                title="支付接口延迟升高",
                summary="支付请求耗时超过阈值",
                facts={"alertname": "PaymentLatencyHigh", "symptom": "latency"},
            )
        ],
        "alertmanager-adapter",
        "req-payment",
    )
    second = grouping.process(
        jobs.claim_batch("grouping-runner", clock[0], limit=1, lease_seconds=30)[0]
    )

    assert first.action == "CREATE_GROUP"
    assert second.action == "JOIN_GROUP"
    assert second.group_id == first.group_id
    assert second.reason_code == "high_confidence_event_match"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AlertGroupRow)) == 1
        assert session.scalar(select(func.count()).select_from(AlertGroupMemberRow)) == 2
        assert session.scalar(select(func.count()).select_from(AlertEventProfileRow)) == 2
        latest_profile = session.scalars(
            select(AlertEventProfileRow).order_by(AlertEventProfileRow.profile_version.desc())
        ).first()
        assert latest_profile is not None
        assert latest_profile.topology_edges == [
            {"caller": "payment-api", "dependency": "business-mysql"}
        ]
        assert (
            session.scalar(select(func.count()).select_from(AlertEventMembershipDecisionRow)) == 2
        )


def test_same_problem_from_different_environments_never_joins(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    development_source_id = "src_00000000000000000000000000000005"
    seed_source(
        session_factory,
        source_id=development_source_id,
        name="开发环境监控",
        environment="development",
    )
    clock = [NOW + timedelta(minutes=1)]
    intake, jobs, grouping = services(session_factory, clock)

    intake.submit_batch([command(111)], "alertmanager-adapter", "req-production")
    production = grouping.process(
        jobs.claim_batch("grouping-runner", clock[0], limit=1, lease_seconds=30)[0]
    )
    intake.submit_batch(
        [command(112, alert_source_id=development_source_id)],
        "alertmanager-adapter",
        "req-development",
    )
    development = grouping.process(
        jobs.claim_batch("grouping-runner", clock[0], limit=1, lease_seconds=30)[0]
    )

    assert production.group_id != development.group_id
    with session_factory() as session:
        assert set(session.scalars(select(AlertGroupRow.environment))) == {
            "production",
            "development",
        }


def test_unconfigured_environment_sources_only_group_within_the_same_source(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    source_ids = (
        "src_00000000000000000000000000000004",
        "src_00000000000000000000000000000005",
    )
    for index, source_id in enumerate(source_ids, start=1):
        seed_source(
            session_factory,
            source_id=source_id,
            name=f"待配置来源 {index}",
            environment="unknown",
            environment_configured=False,
        )
    clock = [NOW + timedelta(minutes=1)]
    intake, jobs, grouping = services(session_factory, clock)

    group_ids: list[str | None] = []
    for index, source_id in enumerate(source_ids, start=301):
        intake.submit_batch(
            [command(index, alert_source_id=source_id, environment="unknown")],
            "alertmanager-adapter",
            f"req-unconfigured-{index}",
        )
        result = grouping.process(
            jobs.claim_batch("grouping-runner", clock[0], limit=1, lease_seconds=30)[0]
        )
        group_ids.append(result.group_id)

    assert group_ids[0] != group_ids[1]
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AlertGroupRow)) == 2


def test_equal_topology_candidates_are_persisted_for_confirmation(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    seed_ambiguous_topology(session_factory)
    clock = [NOW + timedelta(minutes=1)]
    intake, jobs, grouping = services(session_factory, clock)

    for number, service, alertname, symptom in (
        (201, "business-mysql", "MySQLRowLockWaitActive", "lock"),
        (202, "business-redis", "RedisRejectedConnections", "connections"),
    ):
        intake.submit_batch(
            [
                command(
                    number,
                    service=service,
                    title=alertname,
                    summary=alertname,
                    facts={"alertname": alertname, "symptom": symptom},
                )
            ],
            "alertmanager-adapter",
            f"req-{number}",
        )
        result = grouping.process(
            jobs.claim_batch("grouping-runner", clock[0], limit=1, lease_seconds=30)[0]
        )
        assert result.action == "CREATE_GROUP"

    intake.submit_batch(
        [
            command(
                203,
                service="payment-api",
                title="支付接口依赖异常",
                summary="支付接口的下游依赖异常",
                facts={"alertname": "PaymentDependencyFailure", "symptom": "dependency"},
            )
        ],
        "alertmanager-adapter",
        "req-ambiguous",
    )
    pending = grouping.process(
        jobs.claim_batch("grouping-runner", clock[0], limit=1, lease_seconds=30)[0]
    )

    assert pending.action == "PENDING_CONFIRMATION"
    assert pending.group_id is None
    assert pending.reason_code == "candidate_scores_too_close"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AlertGroupRow)) == 2
        assert session.scalar(select(func.count()).select_from(AlertGroupMemberRow)) == 2
        decision = session.scalar(
            select(AlertEventMembershipDecisionRow).where(
                AlertEventMembershipDecisionRow.state == "PENDING"
            )
        )
        assert decision is not None
        assert len(decision.candidate_group_ids) == 2
        assert set(decision.scores) == set(decision.candidate_group_ids)


def test_two_similar_alerts_converge_to_one_explainable_group(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    seed_catalog(session_factory)
    clock = [NOW + timedelta(minutes=1)]
    intake, jobs, grouping = services(session_factory, clock)
    intake.submit_batch([command(1), command(2)], "alertmanager-adapter", "req-1")

    leases = jobs.claim_batch("grouping-runner", clock[0], limit=10, lease_seconds=30)
    results = [grouping.process(lease) for lease in leases]

    assert {result.action for result in results} == {"CREATE_GROUP", "JOIN_GROUP"}
    with session_factory() as session:
        group = session.scalar(select(AlertGroupRow))
        assert group is not None
        assert group.total_count == 2
        assert group.active_count == 2
        assert group.impacted_resource_count == 2
        assert group.state == "FORMING"
        assert group.forming_until == clock[0] + timedelta(seconds=30)
        assert group.symptom == "error_rate"
        assert group.reason_codes == ["high_confidence_event_match"]
        assert group.rule_version == "alert-event-clustering.v1"
        assert session.scalar(select(func.count()).select_from(AlertGroupMemberRow)) == 2
        assert session.scalar(select(func.count()).select_from(CorrelationJobRow)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(AuditEventRow.action == "alert.grouped")
            )
            == 2
        )


def test_alerts_without_service_converge_by_problem_signature(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    clock = [NOW + timedelta(minutes=1)]
    intake, jobs, grouping = services(session_factory, clock)
    intake.submit_batch(
        [
            command(
                30,
                service=None,
                facts={
                    "alertname": "KubePodNotReady",
                    "namespace": "payments",
                    "pod": "payment-worker-0",
                },
            ),
            command(
                31,
                service=None,
                facts={
                    "alertname": "KubePodNotReady",
                    "namespace": "payments",
                    "pod": "payment-worker-1",
                },
            ),
        ],
        "alertmanager-adapter",
        "req-no-service",
    )

    try:
        leases = jobs.claim_batch("grouping-runner", clock[0], limit=10, lease_seconds=30)
        results = [grouping.process(lease) for lease in leases]

        assert {result.action for result in results} == {"CREATE_GROUP", "JOIN_GROUP"}
        with session_factory() as session:
            group = session.scalar(select(AlertGroupRow))
            assert group is not None
            assert group.service is None
            assert group.entity_type == "POD"
            assert group.problem_type == "KubePodNotReady"
            assert group.scope_type == "NAMESPACE"
            assert group.scope_display_name == "payments"
            assert group.total_count == 2
            assert group.active_count == 2
            assert session.scalar(select(func.count()).select_from(AlertGroupMemberRow)) == 2
            assert session.scalar(select(func.count()).select_from(CorrelationJobRow)) == 0
    finally:
        # 0007 的降级保护会拒绝仍含空 service 的测试数据。
        with session_factory() as session:
            session.execute(
                update(SignalEventRow)
                .where(SignalEventRow.service.is_(None))
                .values(service="test-cleanup")
            )
            session.execute(
                update(AlertRow).where(AlertRow.service.is_(None)).values(service="test-cleanup")
            )
            session.execute(
                update(AlertGroupRow)
                .where(AlertGroupRow.service.is_(None))
                .values(service="test-cleanup")
            )
            session.commit()


def test_superseded_job_is_safe_and_resolution_updates_group_without_closing_incident(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    seed_catalog(session_factory)
    clock = [NOW + timedelta(minutes=1)]
    intake, jobs, grouping = services(session_factory, clock)
    initial = command(1)
    intake.submit_batch([initial], "alertmanager-adapter", "req-1")
    intake.submit_batch(
        [
            initial.model_copy(
                update={
                    "source_event_id": "f" * 64,
                    "event_at": NOW + timedelta(seconds=20),
                }
            )
        ],
        "alertmanager-adapter",
        "req-2",
    )
    leases = sorted(
        jobs.claim_batch("grouping-runner", clock[0], limit=10, lease_seconds=30),
        key=lambda item: item.alert_version,
    )

    assert grouping.process(leases[0]).action == "SUPERSEDED"
    assert grouping.process(leases[1]).action == "CREATE_GROUP"

    clock[0] = NOW + timedelta(minutes=2)
    intake.submit_batch(
        [
            initial.model_copy(
                update={
                    "source_event_id": "e" * 64,
                    "event_type": "alert.resolved",
                    "event_at": NOW + timedelta(seconds=30),
                }
            )
        ],
        "alertmanager-adapter",
        "req-3",
    )
    resolution_lease = jobs.claim_batch("grouping-runner", clock[0], limit=1, lease_seconds=30)[0]
    result = grouping.process(resolution_lease)

    assert result.action == "KEEP_GROUP"
    with session_factory() as session:
        group = session.scalar(select(AlertGroupRow))
        assert group is not None
        assert group.state == "OBSERVING"
        assert group.observing_until == clock[0] + timedelta(minutes=5)
        assert group.active_count == 0
        assert group.total_count == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(AlertGroupingJobRow)
                .where(AlertGroupingJobRow.state == "SUCCEEDED")
            )
            == 3
        )


def test_concurrent_similar_jobs_converge_via_catalog_lock(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    seed_catalog(session_factory)
    clock = [NOW + timedelta(minutes=1)]
    intake, jobs, grouping = services(session_factory, clock)
    intake.submit_batch([command(10), command(11)], "alertmanager-adapter", "req-1")
    leases = jobs.claim_batch("grouping-runner", clock[0], limit=10, lease_seconds=30)
    ready = Barrier(2)

    def process(lease_index: int) -> str:
        ready.wait(timeout=5)
        return grouping.process(leases[lease_index]).action

    with ThreadPoolExecutor(max_workers=2) as executor:
        actions = list(executor.map(process, (0, 1)))

    assert set(actions) == {"CREATE_GROUP", "JOIN_GROUP"}
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AlertGroupRow)) == 1
        assert session.scalar(select(func.count()).select_from(AlertGroupMemberRow)) == 2


def test_group_refresh_uses_bounded_queries_instead_of_querying_every_member(
    migrated_engine: Engine,
) -> None:
    session_factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    seed_catalog(session_factory)
    clock = [NOW + timedelta(minutes=1)]
    intake, jobs, grouping = services(session_factory, clock)
    intake.submit_batch(
        [command(index) for index in range(1, 22)], "alertmanager-adapter", "req-bounded"
    )
    leases = jobs.claim_batch("grouping-runner", clock[0], limit=50, lease_seconds=30)
    for lease in leases[:-1]:
        grouping.process(lease)

    selects = 0

    def count_selects(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(migrated_engine, "before_cursor_execute", count_selects)
    try:
        grouping.process(leases[-1])
    finally:
        event.remove(migrated_engine, "before_cursor_execute", count_selects)

    assert selects <= 18
