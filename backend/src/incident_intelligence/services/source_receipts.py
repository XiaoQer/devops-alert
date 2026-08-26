from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from incident_intelligence.domain.alert_sources import AlertSourceType, ReceiptOutcome
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.alert_source_repository import AlertSourceRepository
from incident_intelligence.persistence.models import AlertSourceReceiptRow
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork

RECEIPT_RETENTION_DAYS = 30
RECEIPT_MAX_PER_SOURCE = 1_000


@dataclass(frozen=True, slots=True)
class ReceiptContext:
    alert_source_id: str
    adapter_type: AlertSourceType
    request_id: str
    received_at: datetime


@dataclass(frozen=True, slots=True)
class ReceiptCounts:
    opened: int = 0
    updated: int = 0
    resolved: int = 0
    replayed: int = 0
    ignored: int = 0


class SourceReceiptService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[IdPrefix], str] = new_id,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory

    def record(
        self,
        context: ReceiptContext,
        *,
        outcome: ReceiptOutcome,
        reason_code: str,
        input_count: int,
        counts: ReceiptCounts | None = None,
    ) -> None:
        with self._uow_factory() as uow:
            record_receipt(
                _sources(uow),
                id_factory=self._id_factory,
                context=context,
                outcome=outcome,
                reason_code=reason_code,
                input_count=input_count,
                counts=counts or ReceiptCounts(),
            )
            uow.commit()


def record_receipt(
    sources: AlertSourceRepository,
    *,
    id_factory: Callable[[IdPrefix], str],
    context: ReceiptContext,
    outcome: ReceiptOutcome,
    reason_code: str,
    input_count: int,
    counts: ReceiptCounts,
) -> None:
    source = sources.find_source(context.alert_source_id, for_update=True)
    if source is None:
        raise RuntimeError("接收记录引用的告警源不存在")
    received_at = context.received_at.astimezone(UTC)
    sources.add_receipt(
        AlertSourceReceiptRow(
            id=id_factory("rcp"),
            alert_source_id=source.id,
            adapter_type=context.adapter_type,
            outcome=outcome,
            reason_code=reason_code,
            request_id=context.request_id,
            input_count=input_count,
            opened_count=counts.opened,
            updated_count=counts.updated,
            resolved_count=counts.resolved,
            replayed_count=counts.replayed,
            ignored_count=counts.ignored,
            received_at=received_at,
        )
    )
    if outcome in {"ACCEPTED", "REPLAYED", "VALIDATED"}:
        source.accepted_requests += 1
    else:
        source.rejected_requests += 1
        source.last_rejected_at = received_at
    if outcome in {"ACCEPTED", "REPLAYED"}:
        source.last_accepted_at = received_at
    if outcome == "VALIDATED":
        source.last_validated_at = received_at
    source.opened_count += counts.opened
    source.updated_count += counts.updated
    source.resolved_count += counts.resolved
    source.replayed_count += counts.replayed
    source.ignored_count += counts.ignored
    sources.flush()
    sources.prune_receipts(
        source.id,
        cutoff=received_at - timedelta(days=RECEIPT_RETENTION_DAYS),
        keep=RECEIPT_MAX_PER_SOURCE,
    )


def _sources(uow: SqlAlchemyUnitOfWork) -> AlertSourceRepository:
    if uow.alert_sources is None:
        raise RuntimeError("工作单元没有可用告警源仓储")
    return uow.alert_sources
