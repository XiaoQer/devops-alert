from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.persistence.catalog_repository import ServiceCatalogRepository
from incident_intelligence.persistence.repositories import RecordRepositories


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None
        self.records: RecordRepositories | None = None
        self.catalog: ServiceCatalogRepository | None = None

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        self.session = self._session_factory()
        self.records = RecordRepositories(self.session)
        self.catalog = ServiceCatalogRepository(self.session)
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
