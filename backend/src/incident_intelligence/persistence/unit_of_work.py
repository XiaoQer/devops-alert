from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.persistence.alert_lifecycle_repository import (
    AlertLifecycleRepository,
)
from incident_intelligence.persistence.alert_source_repository import AlertSourceRepository
from incident_intelligence.persistence.incident_rule_repository import IncidentRuleRepository
from incident_intelligence.persistence.repositories import RecordRepositories


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None
        self.records: RecordRepositories | None = None
        self.alert_sources: AlertSourceRepository | None = None
        self.alert_lifecycles: AlertLifecycleRepository | None = None
        self.incident_rules: IncidentRuleRepository | None = None

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        self.session = self._session_factory()
        self.records = RecordRepositories(self.session)
        self.alert_sources = AlertSourceRepository(self.session)
        self.alert_lifecycles = AlertLifecycleRepository(self.session)
        self.incident_rules = IncidentRuleRepository(self.session)
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
