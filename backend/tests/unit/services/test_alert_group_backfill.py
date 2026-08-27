from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from incident_intelligence.services.alert_group_backfill import AlertGroupBackfillService

NOW = datetime(2026, 8, 27, tzinfo=UTC)


class FakeRepository:
    def __init__(self) -> None:
        self.enqueued = 0

    def backfill_candidates(self, *, limit: int) -> tuple[SimpleNamespace, ...]:
        assert limit == 1
        return (SimpleNamespace(id="alt_" + "a" * 32, cycle=1, version=1),)

    def enqueue_grouping(self, **_values: object) -> None:
        self.enqueued += 1


class FakeUnitOfWork:
    def __init__(self, factory: "FakeFactory") -> None:
        self.factory = factory
        self.alert_groups = FakeRepository()

    def __enter__(self) -> "FakeUnitOfWork":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def commit(self) -> None:
        self.factory.commits += 1
        if self.factory.errors:
            raise self.factory.errors.pop(0)


class FakeFactory:
    def __init__(self, errors: list[OperationalError]) -> None:
        self.errors = errors
        self.commits = 0

    def __call__(self) -> FakeUnitOfWork:
        return FakeUnitOfWork(self)


def database_error(code: int) -> OperationalError:
    return OperationalError("statement", {}, Exception(code, "database error"))


def test_mysql_deadlock_retries_with_a_fresh_transaction() -> None:
    factory = FakeFactory([database_error(1213)])
    service = AlertGroupBackfillService(uow_factory=factory)  # type: ignore[arg-type]

    assert service.enqueue_batch(limit=1, now=NOW) == 1
    assert factory.commits == 2


def test_non_lock_database_error_is_not_retried() -> None:
    factory = FakeFactory([database_error(1062)])
    service = AlertGroupBackfillService(uow_factory=factory)  # type: ignore[arg-type]

    with pytest.raises(OperationalError):
        service.enqueue_batch(limit=1, now=NOW)
    assert factory.commits == 1
