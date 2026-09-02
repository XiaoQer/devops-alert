from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.persistence.alert_center_repository import AlertRepository
from incident_intelligence.persistence.alert_lifecycle_repository import (
    AlertLifecycleRepository,
)
from incident_intelligence.persistence.alert_source_repository import AlertSourceRepository
from incident_intelligence.persistence.evidence_repository import (
    EvidenceOperationRepository,
    EvidenceRunRepository,
    EvidenceTaskRepository,
    MonitoringDataSourceRepository,
)
from incident_intelligence.persistence.incident_repository import (
    FeishuEventReceiptRepository,
    IncidentEvaluationJobRepository,
    IncidentFeishuThreadRepository,
    IncidentNotificationRepository,
    IncidentNotificationRouteOperationRepository,
    IncidentNotificationRouteRepository,
    IncidentOperationRepository,
    IncidentRepository,
)
from incident_intelligence.persistence.incident_rule_repository import IncidentRuleRepository
from incident_intelligence.persistence.repositories import RecordRepositories


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None
        self.records: RecordRepositories | None = None
        self.alert_sources: AlertSourceRepository | None = None
        self.alerts: AlertRepository | None = None
        self.alert_lifecycles: AlertLifecycleRepository | None = None
        self.incident_rules: IncidentRuleRepository | None = None
        self.incidents: IncidentRepository | None = None
        self.incident_evaluation_jobs: IncidentEvaluationJobRepository | None = None
        self.incident_notifications: IncidentNotificationRepository | None = None
        self.incident_operations: IncidentOperationRepository | None = None
        self.incident_notification_routes: IncidentNotificationRouteRepository | None = None
        self.incident_notification_route_operations: (
            IncidentNotificationRouteOperationRepository | None
        ) = None
        self.incident_feishu_threads: IncidentFeishuThreadRepository | None = None
        self.feishu_event_receipts: FeishuEventReceiptRepository | None = None
        self.monitoring_data_sources: MonitoringDataSourceRepository | None = None
        self.evidence_runs: EvidenceRunRepository | None = None
        self.evidence_tasks: EvidenceTaskRepository | None = None
        self.evidence_operations: EvidenceOperationRepository | None = None

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        self.session = self._session_factory()
        self.records = RecordRepositories(self.session)
        self.alert_sources = AlertSourceRepository(self.session)
        self.alerts = AlertRepository(self.session)
        self.alert_lifecycles = AlertLifecycleRepository(self.session)
        self.incident_rules = IncidentRuleRepository(self.session)
        self.incidents = IncidentRepository(self.session)
        self.incident_evaluation_jobs = IncidentEvaluationJobRepository(self.session)
        self.incident_notifications = IncidentNotificationRepository(self.session)
        self.incident_operations = IncidentOperationRepository(self.session)
        self.incident_notification_routes = IncidentNotificationRouteRepository(self.session)
        self.incident_notification_route_operations = IncidentNotificationRouteOperationRepository(
            self.session
        )
        self.incident_feishu_threads = IncidentFeishuThreadRepository(self.session)
        self.feishu_event_receipts = FeishuEventReceiptRepository(self.session)
        self.monitoring_data_sources = MonitoringDataSourceRepository(self.session)
        self.evidence_runs = EvidenceRunRepository(self.session)
        self.evidence_tasks = EvidenceTaskRepository(self.session)
        self.evidence_operations = EvidenceOperationRepository(self.session)
        return self

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("工作单元尚未进入事务上下文")
        self.session.commit()

    def rollback(self) -> None:
        if self.session is not None:
            self.session.rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.session is None:
            return
        if exc_type is not None:
            self.session.rollback()
        self.session.close()
