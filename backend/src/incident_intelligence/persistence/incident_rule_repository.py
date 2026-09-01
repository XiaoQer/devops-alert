from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from incident_intelligence.domain.incident_rules import IncidentRule, IncidentRuleConfig
from incident_intelligence.persistence.models import (
    IncidentRuleDryRunRow,
    IncidentRuleOperationRow,
    IncidentRuleRow,
)


@dataclass(frozen=True, slots=True)
class IncidentRulePage:
    items: tuple[IncidentRule, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class IncidentRuleDryRunRecord:
    id: str
    rule_id: str
    rule_version: int
    history_hours: int
    scanned_alert_count: int
    match_count: int
    truncated: bool
    matches: tuple[dict[str, object], ...]
    executed_at: datetime


@dataclass(frozen=True, slots=True)
class IncidentRuleOperationRecord:
    id: str
    scope: str
    idempotency_key_hash: str
    command_fingerprint: str
    action: str
    rule_id: str
    result_version: int
    actor: str
    request_id: str
    summary: str
    completed_at: datetime


class IncidentRuleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, rule: IncidentRule) -> None:
        self._session.add(IncidentRuleRow(**_rule_values(rule)))
        self._session.flush()

    def get(self, rule_id: str, *, for_update: bool = False) -> IncidentRule | None:
        statement = select(IncidentRuleRow).where(IncidentRuleRow.id == rule_id)
        if for_update:
            statement = statement.with_for_update()
        row = self._session.scalar(statement)
        return None if row is None else _to_rule(row)

    def get_by_name(self, name: str) -> IncidentRule | None:
        row = self._session.scalar(select(IncidentRuleRow).where(IncidentRuleRow.name == name))
        return None if row is None else _to_rule(row)

    def list(
        self,
        *,
        state: str | None,
        limit: int,
        offset: int,
    ) -> IncidentRulePage:
        statement = select(IncidentRuleRow)
        count_statement = select(func.count()).select_from(IncidentRuleRow)
        if state is not None:
            statement = statement.where(IncidentRuleRow.state == state)
            count_statement = count_statement.where(IncidentRuleRow.state == state)
        rows = self._session.scalars(
            statement.order_by(IncidentRuleRow.updated_at.desc(), IncidentRuleRow.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return IncidentRulePage(
            items=tuple(_to_rule(row) for row in rows),
            total=self._session.scalar(count_statement) or 0,
            limit=limit,
            offset=offset,
        )

    def list_published(self, *, limit: int = 101) -> tuple[IncidentRule, ...]:
        rows = self._session.scalars(
            select(IncidentRuleRow)
            .where(IncidentRuleRow.state == "PUBLISHED")
            .order_by(IncidentRuleRow.id)
            .limit(limit)
        )
        return tuple(_to_rule(row) for row in rows)

    def update(self, rule: IncidentRule, *, expected_version: int) -> bool:
        values = _rule_values(rule)
        values.pop("id")
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(IncidentRuleRow)
                .where(
                    IncidentRuleRow.id == rule.id,
                    IncidentRuleRow.version == expected_version,
                )
                .values(**values)
            ),
        )
        self._session.flush()
        return result.rowcount == 1

    def delete_draft(self, rule_id: str, *, expected_version: int) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                delete(IncidentRuleRow).where(
                    IncidentRuleRow.id == rule_id,
                    IncidentRuleRow.state == "DRAFT",
                    IncidentRuleRow.version == expected_version,
                )
            ),
        )
        self._session.flush()
        return result.rowcount == 1

    def insert_dry_run(self, record: IncidentRuleDryRunRecord) -> None:
        values = asdict(record)
        values["matches"] = list(record.matches)
        self._session.add(IncidentRuleDryRunRow(**values))
        self._session.flush()

    def insert_operation(self, record: IncidentRuleOperationRecord) -> None:
        self._session.add(IncidentRuleOperationRow(**asdict(record)))
        self._session.flush()

    def find_operation(
        self,
        *,
        scope: str,
        idempotency_key_hash: str,
    ) -> IncidentRuleOperationRecord | None:
        row = self._session.scalar(
            select(IncidentRuleOperationRow).where(
                IncidentRuleOperationRow.scope == scope,
                IncidentRuleOperationRow.idempotency_key_hash == idempotency_key_hash,
            )
        )
        if row is None:
            return None
        return IncidentRuleOperationRecord(
            **{
                column.name: getattr(row, column.name)
                for column in IncidentRuleOperationRow.__table__.columns
            }
        )


def _rule_values(rule: IncidentRule) -> dict[str, object]:
    return {
        "id": rule.id,
        "name": rule.name,
        "description": rule.description,
        "state": rule.state,
        "environment": rule.config.environment,
        "alert_source_ids": list(rule.config.alert_source_ids),
        "services": list(rule.config.services),
        "group_by": rule.config.group_by,
        "window_minutes": rule.config.window_minutes,
        "conditions": [condition.model_dump(mode="json") for condition in rule.config.conditions],
        "summary": rule.summary,
        "version": rule.version,
        "last_successful_dry_run_id": rule.last_successful_dry_run_id,
        "last_successful_dry_run_version": rule.last_successful_dry_run_version,
        "last_successful_dry_run_at": rule.last_successful_dry_run_at,
        "published_at": rule.published_at,
        "disabled_at": rule.disabled_at,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


def _to_rule(row: IncidentRuleRow) -> IncidentRule:
    config = IncidentRuleConfig.model_validate(
        {
            "environment": row.environment,
            "alert_source_ids": row.alert_source_ids,
            "services": row.services,
            "group_by": row.group_by,
            "window_minutes": row.window_minutes,
            "conditions": row.conditions,
        }
    )
    return IncidentRule.model_validate(
        {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "state": row.state,
            "config": config,
            "summary": row.summary,
            "version": row.version,
            "last_successful_dry_run_id": row.last_successful_dry_run_id,
            "last_successful_dry_run_version": row.last_successful_dry_run_version,
            "last_successful_dry_run_at": row.last_successful_dry_run_at,
            "published_at": row.published_at,
            "disabled_at": row.disabled_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )
