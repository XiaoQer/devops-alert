from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import cast

from sqlalchemy.exc import OperationalError

from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork


class AlertGroupBackfillService:
    def __init__(self, *, uow_factory: Callable[[], SqlAlchemyUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def enqueue_batch(self, *, limit: int, now: datetime) -> int:
        if not 1 <= limit <= 100:
            raise ValueError("alert_group_backfill_limit_out_of_range")
        for attempt in range(3):
            try:
                return self._enqueue_once(limit=limit, now=now)
            except OperationalError as error:
                if attempt == 2 or not _is_retryable_mysql_lock_error(error):
                    raise
        raise RuntimeError("alert_group_backfill_retry_exhausted")

    def _enqueue_once(self, *, limit: int, now: datetime) -> int:
        with self._uow_factory() as uow:
            if uow.alert_groups is None:
                raise RuntimeError("工作单元没有可用告警组仓储")
            candidates = uow.alert_groups.backfill_candidates(limit=limit)
            for alert in candidates:
                uow.alert_groups.enqueue_grouping(
                    alert_id=alert.id,
                    alert_cycle=alert.cycle,
                    alert_version=alert.version,
                    now=now,
                )
            uow.commit()
            return len(candidates)


def _is_retryable_mysql_lock_error(error: OperationalError) -> bool:
    arguments = cast(tuple[object, ...], getattr(error.orig, "args", ()))
    return bool(arguments) and arguments[0] in {1205, 1213}
