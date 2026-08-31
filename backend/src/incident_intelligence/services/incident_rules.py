from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from incident_intelligence.domain.incident_rule_evaluation import (
    EvaluationMatch,
    evaluate_rule,
)
from incident_intelligence.domain.incident_rules import (
    IncidentRule,
    IncidentRuleConfig,
    IncidentRuleState,
    RuleDescription,
    RuleName,
    RuleStateConflict,
    copy_rule,
    create_rule,
    disable_rule,
    publish_rule,
    record_successful_dry_run,
    update_draft,
)
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.alert_center_repository import AlertRepository
from incident_intelligence.persistence.incident_rule_repository import (
    IncidentRuleDryRunRecord,
    IncidentRuleOperationRecord,
    IncidentRulePage,
    IncidentRuleRepository,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork

HistoryHours = Literal[1, 6, 12, 24, 48]
OperationAction = Literal["CREATE", "UPDATE", "DELETE", "PUBLISH", "DISABLE", "COPY"]


class _FrozenServiceModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CreateIncidentRuleCommand(_FrozenServiceModel):
    name: RuleName
    description: RuleDescription
    config: IncidentRuleConfig


class IncidentRuleView(IncidentRule):
    publishable: bool


class IncidentRuleMutationResult(_FrozenServiceModel):
    rule: IncidentRuleView
    replayed: bool


class IncidentRulePageView(_FrozenServiceModel):
    items: tuple[IncidentRuleView, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=10_000)


class IncidentRuleDryRunResult(_FrozenServiceModel):
    id: str = Field(pattern=r"^ird_[0-9a-f]{32}$")
    rule: IncidentRuleView
    rule_version: int = Field(ge=1)
    history_hours: HistoryHours
    scanned_alert_count: int = Field(ge=0, le=10_001)
    match_count: int = Field(ge=0, le=100)
    truncated: bool
    matches: tuple[EvaluationMatch, ...]
    executed_at: datetime


class IncidentRuleDeleteResult(_FrozenServiceModel):
    rule_id: str = Field(pattern=r"^irl_[0-9a-f]{32}$")
    deleted: bool
    replayed: bool


@dataclass(frozen=True, slots=True)
class IncidentRuleNotFound(Exception):
    reason_code: str = "incident_rule_not_found"


@dataclass(frozen=True, slots=True)
class IncidentRuleConflict(Exception):
    reason_code: str = "incident_rule_conflict"


@dataclass(frozen=True, slots=True)
class IncidentRuleVersionConflict(Exception):
    reason_code: str = "incident_rule_version_conflict"


@dataclass(frozen=True, slots=True)
class IncidentRulePublishBlocked(Exception):
    reason_code: str = "incident_rule_dry_run_required"


class IncidentRuleService:
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

    def create(
        self,
        command: CreateIncidentRuleCommand,
        *,
        idempotency_key: str,
        actor: str,
        request_id: str,
    ) -> IncidentRuleMutationResult:
        scope = "incident-rule:create"
        key_hash, fingerprint = _operation_identity(scope, idempotency_key, command)
        try:
            with self._uow_factory() as uow:
                repository = _rules(uow)
                replay = self._mutation_replay(repository, scope, key_hash, fingerprint)
                if replay is not None:
                    return replay
                if repository.get_by_name(command.name) is not None:
                    raise IncidentRuleConflict("incident_rule_name_conflict")
                now = self._now()
                rule = create_rule(
                    rule_id=self._id_factory("irl"),
                    name=command.name,
                    description=command.description,
                    config=command.config,
                    now=now,
                )
                repository.insert(rule)
                self._record_operation(
                    repository,
                    scope=scope,
                    key_hash=key_hash,
                    fingerprint=fingerprint,
                    action="CREATE",
                    rule=rule,
                    actor=actor,
                    request_id=request_id,
                    now=now,
                )
                uow.commit()
                return _mutation(rule)
        except IntegrityError as error:
            replay = self._replay_after_conflict(scope, key_hash, fingerprint)
            if replay is not None:
                return replay
            raise IncidentRuleConflict("incident_rule_name_conflict") from error

    def list(
        self,
        *,
        state: IncidentRuleState | None,
        limit: int,
        offset: int,
    ) -> IncidentRulePageView:
        with self._uow_factory() as uow:
            page = _rules(uow).list(state=state, limit=limit, offset=offset)
            return _page_view(page)

    def get(self, rule_id: str) -> IncidentRuleView:
        with self._uow_factory() as uow:
            return _view(_required_rule(_rules(uow), rule_id))

    def update(
        self,
        rule_id: str,
        *,
        expected_version: int,
        name: str,
        description: str,
        config: IncidentRuleConfig,
        idempotency_key: str,
        actor: str,
        request_id: str,
    ) -> IncidentRuleMutationResult:
        payload = {
            "expected_version": expected_version,
            "name": name,
            "description": description,
            "config": config.model_dump(mode="json"),
        }
        return self._change(
            rule_id,
            action="UPDATE",
            expected_version=expected_version,
            payload=payload,
            idempotency_key=idempotency_key,
            actor=actor,
            request_id=request_id,
            transition=lambda rule, now: update_draft(
                rule,
                name=name,
                description=description,
                config=config,
                now=now,
            ),
        )

    def dry_run(
        self,
        rule_id: str,
        *,
        expected_version: int,
        history_hours: HistoryHours,
        actor: str,
    ) -> IncidentRuleDryRunResult:
        del actor
        with self._uow_factory() as uow:
            repository = _rules(uow)
            rule = _required_rule(repository, rule_id, for_update=True)
            _require_version(rule, expected_version)
            if rule.state != "DRAFT":
                raise IncidentRuleConflict("draft_required")
            now = self._now()
            session = _session(uow)
            alerts = AlertRepository(session).list_for_rule_evaluation(
                environment=rule.config.environment,
                alert_source_ids=rule.config.alert_source_ids,
                services=rule.config.services,
                received_from=now - timedelta(hours=history_hours),
                received_to=now + timedelta(microseconds=1),
                limit=10_001,
            )
            scan_truncated = len(alerts) > 10_000
            evaluation = evaluate_rule(rule.config, alerts[:10_000], max_matches=100)
            truncated = scan_truncated or evaluation.truncated
            dry_run_id = self._id_factory("ird")
            record = IncidentRuleDryRunRecord(
                id=dry_run_id,
                rule_id=rule.id,
                rule_version=rule.version,
                history_hours=history_hours,
                scanned_alert_count=len(alerts),
                match_count=len(evaluation.matches),
                truncated=truncated,
                matches=tuple(match.model_dump(mode="json") for match in evaluation.matches),
                executed_at=now,
            )
            repository.insert_dry_run(record)
            if not truncated:
                tested_rule = record_successful_dry_run(
                    rule,
                    dry_run_id=dry_run_id,
                    now=now,
                )
                if not repository.update(tested_rule, expected_version=rule.version):
                    raise IncidentRuleVersionConflict()
                rule = tested_rule
            uow.commit()
            return IncidentRuleDryRunResult(
                id=dry_run_id,
                rule=_view(rule),
                rule_version=record.rule_version,
                history_hours=history_hours,
                scanned_alert_count=record.scanned_alert_count,
                match_count=record.match_count,
                truncated=truncated,
                matches=evaluation.matches,
                executed_at=now,
            )

    def publish(
        self,
        rule_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
        actor: str,
        request_id: str,
    ) -> IncidentRuleMutationResult:
        return self._change(
            rule_id,
            action="PUBLISH",
            expected_version=expected_version,
            payload={"expected_version": expected_version},
            idempotency_key=idempotency_key,
            actor=actor,
            request_id=request_id,
            transition=lambda rule, now: publish_rule(rule, now=now),
        )

    def disable(
        self,
        rule_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
        actor: str,
        request_id: str,
    ) -> IncidentRuleMutationResult:
        return self._change(
            rule_id,
            action="DISABLE",
            expected_version=expected_version,
            payload={"expected_version": expected_version},
            idempotency_key=idempotency_key,
            actor=actor,
            request_id=request_id,
            transition=lambda rule, now: disable_rule(rule, now=now),
        )

    def copy(
        self,
        rule_id: str,
        *,
        name: str,
        idempotency_key: str,
        actor: str,
        request_id: str,
    ) -> IncidentRuleMutationResult:
        scope = f"incident-rule:{rule_id}:copy"
        key_hash, fingerprint = _operation_identity(scope, idempotency_key, {"name": name})
        try:
            with self._uow_factory() as uow:
                repository = _rules(uow)
                replay = self._mutation_replay(repository, scope, key_hash, fingerprint)
                if replay is not None:
                    return replay
                source = _required_rule(repository, rule_id)
                if repository.get_by_name(name) is not None:
                    raise IncidentRuleConflict("incident_rule_name_conflict")
                now = self._now()
                copied = copy_rule(source, rule_id=self._id_factory("irl"), name=name, now=now)
                repository.insert(copied)
                self._record_operation(
                    repository,
                    scope=scope,
                    key_hash=key_hash,
                    fingerprint=fingerprint,
                    action="COPY",
                    rule=copied,
                    actor=actor,
                    request_id=request_id,
                    now=now,
                )
                uow.commit()
                return _mutation(copied)
        except IntegrityError as error:
            replay = self._replay_after_conflict(scope, key_hash, fingerprint)
            if replay is not None:
                return replay
            raise IncidentRuleConflict("incident_rule_name_conflict") from error

    def delete(
        self,
        rule_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
        actor: str,
        request_id: str,
    ) -> IncidentRuleDeleteResult:
        scope = f"incident-rule:{rule_id}:delete"
        key_hash, fingerprint = _operation_identity(
            scope,
            idempotency_key,
            {"expected_version": expected_version},
        )
        with self._uow_factory() as uow:
            repository = _rules(uow)
            prior = repository.find_operation(scope=scope, idempotency_key_hash=key_hash)
            if prior is not None:
                if prior.command_fingerprint != fingerprint:
                    raise IncidentRuleConflict("idempotency_conflict")
                return IncidentRuleDeleteResult(rule_id=rule_id, deleted=True, replayed=True)
            rule = _required_rule(repository, rule_id, for_update=True)
            _require_version(rule, expected_version)
            if rule.state != "DRAFT":
                raise IncidentRuleConflict("draft_required")
            now = self._now()
            self._record_operation(
                repository,
                scope=scope,
                key_hash=key_hash,
                fingerprint=fingerprint,
                action="DELETE",
                rule=rule,
                actor=actor,
                request_id=request_id,
                now=now,
            )
            if not repository.delete_draft(rule.id, expected_version=expected_version):
                raise IncidentRuleVersionConflict()
            uow.commit()
            return IncidentRuleDeleteResult(rule_id=rule_id, deleted=True, replayed=False)

    def _change(
        self,
        rule_id: str,
        *,
        action: OperationAction,
        expected_version: int,
        payload: dict[str, object],
        idempotency_key: str,
        actor: str,
        request_id: str,
        transition: Callable[[IncidentRule, datetime], IncidentRule],
    ) -> IncidentRuleMutationResult:
        scope = f"incident-rule:{rule_id}:{action.casefold()}"
        key_hash, fingerprint = _operation_identity(scope, idempotency_key, payload)
        with self._uow_factory() as uow:
            repository = _rules(uow)
            replay = self._mutation_replay(repository, scope, key_hash, fingerprint)
            if replay is not None:
                return replay
            rule = _required_rule(repository, rule_id, for_update=True)
            _require_version(rule, expected_version)
            now = self._now()
            try:
                changed = transition(rule, now)
            except RuleStateConflict as error:
                if str(error) == "successful_dry_run_required":
                    raise IncidentRulePublishBlocked() from error
                raise IncidentRuleConflict(str(error)) from error
            if not repository.update(changed, expected_version=expected_version):
                raise IncidentRuleVersionConflict()
            self._record_operation(
                repository,
                scope=scope,
                key_hash=key_hash,
                fingerprint=fingerprint,
                action=action,
                rule=changed,
                actor=actor,
                request_id=request_id,
                now=now,
            )
            uow.commit()
            return _mutation(changed)

    def _record_operation(
        self,
        repository: IncidentRuleRepository,
        *,
        scope: str,
        key_hash: str,
        fingerprint: str,
        action: OperationAction,
        rule: IncidentRule,
        actor: str,
        request_id: str,
        now: datetime,
    ) -> None:
        repository.insert_operation(
            IncidentRuleOperationRecord(
                id=self._id_factory("iro"),
                scope=scope,
                idempotency_key_hash=key_hash,
                command_fingerprint=fingerprint,
                action=action,
                rule_id=rule.id,
                result_version=rule.version,
                actor=actor,
                request_id=request_id,
                summary=f"{action} Incident 规则",
                completed_at=now,
            )
        )

    @staticmethod
    def _mutation_replay(
        repository: IncidentRuleRepository,
        scope: str,
        key_hash: str,
        fingerprint: str,
    ) -> IncidentRuleMutationResult | None:
        operation = repository.find_operation(
            scope=scope,
            idempotency_key_hash=key_hash,
        )
        if operation is None:
            return None
        if operation.command_fingerprint != fingerprint:
            raise IncidentRuleConflict("idempotency_conflict")
        rule = _required_rule(repository, operation.rule_id)
        return _mutation(rule, replayed=True)

    def _replay_after_conflict(
        self,
        scope: str,
        key_hash: str,
        fingerprint: str,
    ) -> IncidentRuleMutationResult | None:
        with self._uow_factory() as uow:
            return self._mutation_replay(_rules(uow), scope, key_hash, fingerprint)

    def _now(self) -> datetime:
        return self._clock().astimezone(UTC)


def _operation_identity(
    scope: str,
    idempotency_key: str,
    payload: BaseModel | dict[str, object],
) -> tuple[str, str]:
    normalized_key = idempotency_key.strip()
    if not 1 <= len(normalized_key) <= 256:
        raise ValueError("幂等键长度必须为 1 到 256")
    body = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (
        sha256(normalized_key.encode()).hexdigest(),
        sha256(f"{scope}:{canonical}".encode()).hexdigest(),
    )


def _rules(uow: SqlAlchemyUnitOfWork) -> IncidentRuleRepository:
    if uow.incident_rules is None:
        raise RuntimeError("工作单元没有可用 Incident 规则仓储")
    return uow.incident_rules


def _session(uow: SqlAlchemyUnitOfWork) -> Session:
    if uow.session is None:
        raise RuntimeError("工作单元没有可用数据库会话")
    return uow.session


def _required_rule(
    repository: IncidentRuleRepository,
    rule_id: str,
    *,
    for_update: bool = False,
) -> IncidentRule:
    rule = repository.get(rule_id, for_update=for_update)
    if rule is None:
        raise IncidentRuleNotFound()
    return rule


def _require_version(rule: IncidentRule, expected_version: int) -> None:
    if rule.version != expected_version:
        raise IncidentRuleVersionConflict()


def _view(rule: IncidentRule) -> IncidentRuleView:
    return IncidentRuleView.model_validate(
        {
            **rule.model_dump(),
            "publishable": (
                rule.state == "DRAFT" and rule.last_successful_dry_run_version == rule.version
            ),
        }
    )


def _mutation(rule: IncidentRule, *, replayed: bool = False) -> IncidentRuleMutationResult:
    return IncidentRuleMutationResult(rule=_view(rule), replayed=replayed)


def _page_view(page: IncidentRulePage) -> IncidentRulePageView:
    return IncidentRulePageView(
        items=tuple(_view(rule) for rule in page.items),
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )
