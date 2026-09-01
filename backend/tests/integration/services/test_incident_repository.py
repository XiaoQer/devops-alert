from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.alerts import Alert
from incident_intelligence.domain.incident_rules import IncidentRuleConfig, create_rule
from incident_intelligence.domain.incidents import (
    IncidentAlertFact,
    IncidentAlertLink,
    create_incident,
    resolve_incident,
)
from incident_intelligence.persistence.alert_lifecycle_repository import AlertLifecycleRepository
from incident_intelligence.persistence.incident_repository import (
    FeishuEventReceiptRecord,
    FeishuEventReceiptRepository,
    IncidentEvaluationJobRecord,
    IncidentEvaluationJobRepository,
    IncidentFeishuThreadRecord,
    IncidentFeishuThreadRepository,
    IncidentNotificationRecord,
    IncidentNotificationRepository,
    IncidentNotificationRouteRecord,
    IncidentNotificationRouteRepository,
    IncidentRepository,
)
from incident_intelligence.persistence.incident_rule_repository import IncidentRuleRepository
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
INCIDENT_ID = "inc_11111111111111111111111111111111"
RULE_ID = "irl_22222222222222222222222222222222"
ALERT_ID = "alt_33333333333333333333333333333333"
ACTIVITY_ID = "iact_44444444444444444444444444444444"


def test_repository_round_trips_incident_and_releases_boundary_on_resolution(
    migrated_engine: Engine,
) -> None:
    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    with factory() as session:
        _insert_rule_and_alert(session)
        change = _incident_change()
        repository = IncidentRepository(session)
        repository.insert(change.incident)
        repository.link_alerts(
            (
                IncidentAlertLink(
                    incident_id=INCIDENT_ID,
                    alert_id=ALERT_ID,
                    incident_rule_version=1,
                    first_trigger_window=True,
                    linked_at=NOW,
                ),
            )
        )
        repository.append_activities(change.activities)
        session.commit()

        found = repository.find_unresolved(
            rule_id=RULE_ID,
            environment="production",
            group_key="checkout",
            for_update=True,
        )
        assert found == change.incident
        assert repository.get(INCIDENT_ID) == change.incident

        resolved = resolve_incident(
            change.incident,
            activity_id="iact_55555555555555555555555555555555",
            resolution_summary="服务已恢复",
            actor="tester",
            now=NOW + timedelta(minutes=1),
        )
        assert repository.update(resolved.incident, expected_version=1) is True
        repository.append_activities(resolved.activities)
        session.commit()
        assert (
            repository.find_unresolved(
                rule_id=RULE_ID,
                environment="production",
                group_key="checkout",
            )
            is None
        )


def test_alert_links_and_activities_are_idempotent_and_ordered(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session:
        _insert_rule_and_alert(session)
        change = _incident_change()
        repository = IncidentRepository(session)
        repository.insert(change.incident)
        link = IncidentAlertLink(
            incident_id=INCIDENT_ID,
            alert_id=ALERT_ID,
            incident_rule_version=1,
            first_trigger_window=True,
            linked_at=NOW,
        )

        assert repository.link_alerts((link,)) == (ALERT_ID,)
        assert repository.link_alerts((link,)) == ()
        repository.append_activities(change.activities)
        repository.append_activities(change.activities)
        session.commit()

        assert repository.list_alert_ids(INCIDENT_ID) == (ALERT_ID,)
        assert repository.list_activities(INCIDENT_ID) == change.activities


def test_evaluation_jobs_enqueue_once_and_lease_due_work(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        _insert_rule_and_alert(session)
        repository = IncidentEvaluationJobRepository(session)
        job = IncidentEvaluationJobRecord(
            id="iej_66666666666666666666666666666666",
            alert_id=ALERT_ID,
            alert_version=1,
            state="PENDING",
            attempt_count=0,
            available_at=NOW,
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=None,
            outcome=None,
            reason_codes=(),
            incident_ids=(),
            created_at=NOW,
            updated_at=NOW,
        )

        assert repository.enqueue(job) is True
        assert repository.enqueue(job) is False
        leased = repository.lease_due(
            owner="worker-1",
            now=NOW,
            lease_until=NOW + timedelta(seconds=30),
            limit=20,
        )
        session.commit()

        assert len(leased) == 1
        assert leased[0].state == "LEASED"
        assert leased[0].attempt_count == 1
        assert leased[0].lease_owner == "worker-1"


def test_evaluation_job_transitions_require_current_lease_owner(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session:
        _insert_rule_and_alert(session)
        repository = IncidentEvaluationJobRepository(session)
        job = _evaluation_job(alert_version=1)
        repository.enqueue(job)
        repository.lease_due(
            owner="worker-1",
            now=NOW,
            lease_until=NOW + timedelta(seconds=30),
            limit=1,
        )

        assert (
            repository.retry(
                job.id,
                owner="stale-worker",
                error_code="mysql_busy",
                available_at=NOW + timedelta(minutes=1),
                now=NOW,
            )
            is False
        )
        assert repository.retry(
            job.id,
            owner="worker-1",
            error_code="mysql_busy",
            available_at=NOW + timedelta(minutes=1),
            now=NOW,
        )
        retried = repository.get(job.id)
        assert retried is not None
        assert retried.state == "PENDING"
        assert retried.last_error_code == "mysql_busy"
        assert retried.lease_owner is None

        repository.lease_due(
            owner="worker-2",
            now=NOW + timedelta(minutes=1),
            lease_until=NOW + timedelta(minutes=2),
            limit=1,
        )
        assert repository.complete(
            job.id,
            owner="worker-2",
            outcome="INCIDENT_CREATED",
            reason_codes=("published_rule_matched",),
            incident_ids=(INCIDENT_ID,),
            now=NOW + timedelta(minutes=1),
        )
        completed = repository.get(job.id)
        assert completed is not None
        assert completed.state == "SUCCEEDED"
        assert completed.incident_ids == (INCIDENT_ID,)
        assert completed.reason_codes == ("published_rule_matched",)


def test_evaluation_job_can_enter_permanent_failed_state(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        _insert_rule_and_alert(session)
        repository = IncidentEvaluationJobRepository(session)
        job = _evaluation_job(alert_version=2)
        repository.enqueue(job)
        repository.lease_due(
            owner="worker-1",
            now=NOW,
            lease_until=NOW + timedelta(seconds=30),
            limit=1,
        )

        assert repository.fail(
            job.id,
            owner="worker-1",
            error_code="retry_exhausted",
            now=NOW,
        )
        failed = repository.get(job.id)
        assert failed is not None
        assert failed.state == "FAILED"
        assert failed.last_error_code == "retry_exhausted"
        assert failed.lease_owner is None


def test_notification_outbox_enqueue_is_idempotent(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        _insert_rule_and_alert(session)
        change = _incident_change()
        incidents = IncidentRepository(session)
        incidents.insert(change.incident)
        incidents.append_activities(change.activities)
        repository = IncidentNotificationRepository(session)
        notification = IncidentNotificationRecord(
            id="ino_77777777777777777777777777777777",
            incident_id=INCIDENT_ID,
            activity_id=ACTIVITY_ID,
            notification_key="8" * 64,
            kind="CREATE_CARD",
            state="PENDING",
            payload={"incident_reference": "INC-20260901-001"},
            attempt_count=0,
            available_at=NOW,
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=None,
            feishu_message_id=None,
            created_at=NOW,
            updated_at=NOW,
        )

        assert repository.enqueue(notification) is True
        assert repository.enqueue(notification) is False
        session.commit()
        assert repository.get(notification.id) == notification


def test_notification_outbox_uses_same_owned_lease_transitions(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session:
        _insert_rule_and_alert(session)
        change = _incident_change()
        incidents = IncidentRepository(session)
        incidents.insert(change.incident)
        incidents.append_activities(change.activities)
        repository = IncidentNotificationRepository(session)
        notification = _notification()
        repository.enqueue(notification)
        leased = repository.lease_due(
            owner="feishu-worker",
            now=NOW,
            lease_until=NOW + timedelta(seconds=30),
            limit=10,
        )
        assert [item.id for item in leased] == [notification.id]
        assert repository.succeed(
            notification.id,
            owner="feishu-worker",
            feishu_message_id="om_root",
            now=NOW,
        )
        delivered = repository.get(notification.id)
        assert delivered is not None
        assert delivered.state == "SUCCEEDED"
        assert delivered.feishu_message_id == "om_root"
        assert delivered.lease_owner is None


def test_notification_outbox_can_retry_and_fail_only_with_owned_lease(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session:
        _insert_rule_and_alert(session)
        change = _incident_change()
        incidents = IncidentRepository(session)
        incidents.insert(change.incident)
        incidents.append_activities(change.activities)
        repository = IncidentNotificationRepository(session)
        first = _notification()
        second = replace(
            first,
            id="ino_cccccccccccccccccccccccccccccccc",
            notification_key="d" * 64,
        )
        repository.enqueue(first)
        repository.enqueue(second)
        repository.lease_due(
            owner="feishu-worker",
            now=NOW,
            lease_until=NOW + timedelta(seconds=30),
            limit=2,
        )

        assert (
            repository.retry(
                first.id,
                owner="stale-worker",
                error_code="rate_limited",
                available_at=NOW + timedelta(minutes=1),
                now=NOW,
            )
            is False
        )
        assert repository.retry(
            first.id,
            owner="feishu-worker",
            error_code="rate_limited",
            available_at=NOW + timedelta(minutes=1),
            now=NOW,
        )
        assert repository.fail(
            second.id,
            owner="feishu-worker",
            error_code="retry_exhausted",
            now=NOW,
        )
        retried = repository.get(first.id)
        failed = repository.get(second.id)
        assert retried is not None and retried.state == "PENDING"
        assert retried.last_error_code == "rate_limited"
        assert retried.lease_owner is None
        assert failed is not None and failed.state == "FAILED"
        assert failed.last_error_code == "retry_exhausted"
        assert failed.lease_owner is None


def test_incident_repository_lists_newest_and_counts_linked_alert_states(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session:
        _insert_rule_and_alert(session)
        change = _incident_change()
        repository = IncidentRepository(session)
        repository.insert(change.incident)
        repository.link_alerts(
            (
                IncidentAlertLink(
                    incident_id=INCIDENT_ID,
                    alert_id=ALERT_ID,
                    incident_rule_version=1,
                    first_trigger_window=True,
                    linked_at=NOW,
                ),
            )
        )
        session.commit()

        page = repository.list(
            states=("OPEN", "ACKNOWLEDGED"),
            environment="production",
            severity="high",
            search="checkout",
            limit=20,
            offset=0,
        )
        counts = repository.count_alert_states(INCIDENT_ID)
        assert page.items == (change.incident,)
        assert page.total == 1
        assert (counts.active, counts.resolved, counts.total) == (1, 0, 1)


def test_incident_reference_sequence_is_monotonic_per_utc_day(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session:
        repository = IncidentRepository(session)

        assert repository.next_reference(NOW) == "INC-20260901-001"
        assert repository.next_reference(NOW + timedelta(minutes=1)) == "INC-20260901-002"
        assert repository.next_reference(NOW + timedelta(days=1)) == "INC-20260902-001"
        session.commit()


def test_route_thread_and_event_receipt_repositories_keep_external_identity_bounded(
    migrated_engine: Engine,
) -> None:
    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    with factory() as session:
        _insert_rule_and_alert(session)
        change = _incident_change()
        IncidentRepository(session).insert(change.incident)
        route = IncidentNotificationRouteRecord(
            id="inr_88888888888888888888888888888888",
            environment="production",
            chat_id="oc_primary",
            chat_name="生产事故群",
            enabled=True,
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
        routes = IncidentNotificationRouteRepository(session)
        routes.insert(route)
        thread = IncidentFeishuThreadRecord(
            id="ift_99999999999999999999999999999999",
            incident_id=INCIDENT_ID,
            route_id=route.id,
            chat_id=route.chat_id,
            root_message_id="om_root",
            last_synced_at=NOW,
            last_error_code=None,
            created_at=NOW,
            updated_at=NOW,
        )
        threads = IncidentFeishuThreadRepository(session)
        threads.insert(thread)
        receipt = FeishuEventReceiptRecord(
            id="fer_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            event_id="event-1",
            event_type="im.message.receive_v1",
            outcome="RECORDED",
            received_at=NOW,
        )
        receipts = FeishuEventReceiptRepository(session)

        assert receipts.record_once(receipt) is True
        assert receipts.record_once(receipt) is False
        session.commit()
        assert routes.find_enabled("production") == route
        assert threads.get_by_incident(INCIDENT_ID) == thread
        assert threads.find_by_message("oc_primary", "om_root") == thread

    with SqlAlchemyUnitOfWork(factory) as uow:
        assert isinstance(uow.incidents, IncidentRepository)
        assert isinstance(uow.incident_evaluation_jobs, IncidentEvaluationJobRepository)
        assert isinstance(uow.incident_notifications, IncidentNotificationRepository)
        assert isinstance(uow.incident_notification_routes, IncidentNotificationRouteRepository)
        assert isinstance(uow.incident_feishu_threads, IncidentFeishuThreadRepository)
        assert isinstance(uow.feishu_event_receipts, FeishuEventReceiptRepository)


def _insert_rule_and_alert(session: Session) -> None:
    IncidentRuleRepository(session).insert(
        create_rule(
            rule_id=RULE_ID,
            name="支付链路异常",
            description="识别支付服务短时间内的多类告警",
            config=IncidentRuleConfig.model_validate(
                {
                    "environment": "production",
                    "alert_source_ids": (),
                    "services": ("checkout",),
                    "group_by": "SERVICE",
                    "window_minutes": 5,
                    "conditions": (
                        {"type": "DISTINCT_ALERT_NAMES_GTE", "threshold": 1},
                    ),
                }
            ),
            now=NOW,
        )
    )
    AlertLifecycleRepository(session).insert(_alert())


def _evaluation_job(*, alert_version: int) -> IncidentEvaluationJobRecord:
    return IncidentEvaluationJobRecord(
        id=f"iej_{alert_version:032x}",
        alert_id=ALERT_ID,
        alert_version=alert_version,
        state="PENDING",
        attempt_count=0,
        available_at=NOW,
        lease_owner=None,
        lease_expires_at=None,
        last_error_code=None,
        outcome=None,
        reason_codes=(),
        incident_ids=(),
        created_at=NOW,
        updated_at=NOW,
    )


def _notification() -> IncidentNotificationRecord:
    return IncidentNotificationRecord(
        id="ino_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        incident_id=INCIDENT_ID,
        activity_id=ACTIVITY_ID,
        notification_key="c" * 64,
        kind="CREATE_CARD",
        state="PENDING",
        payload={"incident_reference": "INC-20260901-001"},
        attempt_count=0,
        available_at=NOW,
        lease_owner=None,
        lease_expires_at=None,
        last_error_code=None,
        feishu_message_id=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _alert() -> Alert:
    return Alert(
        id=ALERT_ID,
        alert_source_id="src_00000000000000000000000000000001",
        source_alert_key="checkout-error",
        episode_started_at=NOW,
        alert_name="HighErrorRate",
        summary="错误率升高",
        description="checkout 错误率超过阈值",
        state="ACTIVE",
        severity="high",
        environment="production",
        service="checkout",
        entity_type="SERVICE",
        entity_key="a" * 64,
        entity_display_name="checkout",
        first_observed_at=NOW,
        last_observed_at=NOW,
        first_received_at=NOW,
        last_received_at=NOW,
        resolved_at=None,
        firing_observed=True,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _incident_change():
    return create_incident(
        incident_id=INCIDENT_ID,
        reference="INC-20260901-001",
        rule_id=RULE_ID,
        rule_version=1,
        environment="production",
        group_by="SERVICE",
        group_key="checkout",
        group_display_name="checkout",
        alerts=(
            IncidentAlertFact(
                id=ALERT_ID,
                alert_name="HighErrorRate",
                state="ACTIVE",
                severity="high",
                first_received_at=NOW,
            ),
        ),
        created_activity_id=ACTIVITY_ID,
        now=NOW,
    )
