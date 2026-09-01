from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError

from incident_intelligence.domain.incident_notifications import (
    IncidentNotificationRoute,
    create_notification_route,
    update_notification_route,
)
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.incident_repository import (
    IncidentNotificationRouteOperationRecord,
    IncidentNotificationRouteOperationRepository,
    IncidentNotificationRouteRecord,
    IncidentNotificationRouteRepository,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.settings import FeishuCapabilityView

RouteAction = Literal["CREATE", "UPDATE"]


class IncidentNotificationRoutePage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[IncidentNotificationRoute, ...]
    total: int
    feishu_capability: FeishuCapabilityView


class IncidentNotificationRouteMutationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    route: IncidentNotificationRoute
    replayed: bool


@dataclass(frozen=True, slots=True)
class IncidentNotificationRouteNotFound(Exception):
    reason_code: str = "incident_notification_route_not_found"


@dataclass(frozen=True, slots=True)
class IncidentNotificationRouteConflict(Exception):
    reason_code: str = "incident_notification_route_conflict"


@dataclass(frozen=True, slots=True)
class IncidentNotificationRouteVersionConflict(Exception):
    reason_code: str = "incident_notification_route_version_conflict"


@dataclass(frozen=True, slots=True)
class IncidentNotificationRouteIdempotencyConflict(Exception):
    reason_code: str = "idempotency_conflict"


@dataclass(frozen=True, slots=True)
class FeishuCredentialsIncomplete(Exception):
    missing_environment_keys: tuple[str, ...]
    reason_code: str = "feishu_credentials_incomplete"


class IncidentNotificationRouteService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        capability: FeishuCapabilityView,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[IdPrefix], str] = new_id,
    ) -> None:
        self._uow_factory = uow_factory
        self._capability = capability
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory

    def list(self) -> IncidentNotificationRoutePage:
        with self._uow_factory() as uow:
            items = tuple(_to_domain(record) for record in _routes(uow).list())
            return IncidentNotificationRoutePage(
                items=items,
                total=len(items),
                feishu_capability=self._capability,
            )

    def create(
        self,
        *,
        environment: str,
        chat_id: str,
        chat_name: str,
        enabled: bool,
        idempotency_key: str,
        actor: str,
        request_id: str,
    ) -> IncidentNotificationRouteMutationResult:
        payload = {
            "environment": environment,
            "chat_id": chat_id,
            "chat_name": chat_name,
            "enabled": enabled,
        }
        return self._execute(
            scope="incident-notification-route:create",
            action="CREATE",
            payload=payload,
            idempotency_key=idempotency_key,
            actor=actor,
            request_id=request_id,
            change=lambda repository, now: self._create_route(
                repository,
                environment=environment,
                chat_id=chat_id,
                chat_name=chat_name,
                enabled=enabled,
                now=now,
            ),
        )

    def update(
        self,
        route_id: str,
        *,
        expected_version: int,
        environment: str,
        chat_id: str,
        chat_name: str,
        enabled: bool,
        idempotency_key: str,
        actor: str,
        request_id: str,
    ) -> IncidentNotificationRouteMutationResult:
        payload = {
            "expected_version": expected_version,
            "environment": environment,
            "chat_id": chat_id,
            "chat_name": chat_name,
            "enabled": enabled,
        }
        return self._execute(
            scope=f"incident-notification-route:{route_id}:update",
            action="UPDATE",
            payload=payload,
            idempotency_key=idempotency_key,
            actor=actor,
            request_id=request_id,
            change=lambda repository, now: self._update_route(
                repository,
                route_id=route_id,
                expected_version=expected_version,
                environment=environment,
                chat_id=chat_id,
                chat_name=chat_name,
                enabled=enabled,
                now=now,
            ),
        )

    def _execute(
        self,
        *,
        scope: str,
        action: RouteAction,
        payload: dict[str, object],
        idempotency_key: str,
        actor: str,
        request_id: str,
        change: Callable[
            [IncidentNotificationRouteRepository, datetime], IncidentNotificationRoute
        ],
    ) -> IncidentNotificationRouteMutationResult:
        key_hash = sha256(idempotency_key.encode()).hexdigest()
        fingerprint = _fingerprint(payload)
        try:
            with self._uow_factory() as uow:
                prior = _operations(uow).find(
                    scope=scope,
                    idempotency_key_hash=key_hash,
                )
                if prior is not None:
                    return self._replay(uow, prior, fingerprint)
                route = change(_routes(uow), self._clock().astimezone(UTC))
                _operations(uow).insert(
                    IncidentNotificationRouteOperationRecord(
                        id=self._id_factory("iop"),
                        scope=scope,
                        idempotency_key_hash=key_hash,
                        command_fingerprint=fingerprint,
                        action=action,
                        route_id=route.id,
                        result_version=route.version,
                        actor=actor,
                        request_id=request_id,
                        completed_at=route.updated_at,
                    )
                )
                uow.commit()
                return IncidentNotificationRouteMutationResult(route=route, replayed=False)
        except IntegrityError:
            replay = self._replay_after_conflict(scope, key_hash, fingerprint)
            if replay is not None:
                return replay
            raise IncidentNotificationRouteConflict() from None

    def _create_route(
        self,
        repository: IncidentNotificationRouteRepository,
        *,
        environment: str,
        chat_id: str,
        chat_name: str,
        enabled: bool,
        now: datetime,
    ) -> IncidentNotificationRoute:
        self._require_capability(enabled)
        route = create_notification_route(
            route_id=self._id_factory("inr"),
            environment=environment,
            chat_id=chat_id,
            chat_name=chat_name,
            enabled=enabled,
            now=now,
        )
        repository.insert(_to_record(route))
        return route

    def _update_route(
        self,
        repository: IncidentNotificationRouteRepository,
        *,
        route_id: str,
        expected_version: int,
        environment: str,
        chat_id: str,
        chat_name: str,
        enabled: bool,
        now: datetime,
    ) -> IncidentNotificationRoute:
        self._require_capability(enabled)
        current_record = repository.get(route_id, for_update=True)
        if current_record is None:
            raise IncidentNotificationRouteNotFound()
        if current_record.version != expected_version:
            raise IncidentNotificationRouteVersionConflict()
        updated = update_notification_route(
            _to_domain(current_record),
            environment=environment,
            chat_id=chat_id,
            chat_name=chat_name,
            enabled=enabled,
            now=now,
        )
        if not repository.update(_to_record(updated), expected_version=expected_version):
            raise IncidentNotificationRouteVersionConflict()
        return updated

    def _require_capability(self, enabled: bool) -> None:
        if enabled and not self._capability.configured:
            raise FeishuCredentialsIncomplete(self._capability.missing_environment_keys)

    def _replay(
        self,
        uow: SqlAlchemyUnitOfWork,
        prior: IncidentNotificationRouteOperationRecord,
        fingerprint: str,
    ) -> IncidentNotificationRouteMutationResult:
        if prior.command_fingerprint != fingerprint:
            raise IncidentNotificationRouteIdempotencyConflict()
        record = _routes(uow).get(prior.route_id)
        if record is None:
            raise IncidentNotificationRouteNotFound()
        return IncidentNotificationRouteMutationResult(
            route=_to_domain(record),
            replayed=True,
        )

    def _replay_after_conflict(
        self,
        scope: str,
        key_hash: str,
        fingerprint: str,
    ) -> IncidentNotificationRouteMutationResult | None:
        with self._uow_factory() as uow:
            prior = _operations(uow).find(
                scope=scope,
                idempotency_key_hash=key_hash,
            )
            return None if prior is None else self._replay(uow, prior, fingerprint)


def _fingerprint(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode()).hexdigest()


def _to_domain(record: IncidentNotificationRouteRecord) -> IncidentNotificationRoute:
    return IncidentNotificationRoute.model_validate(
        {
            **asdict(record),
            "enabled_environment_key": record.environment if record.enabled else None,
        }
    )


def _to_record(route: IncidentNotificationRoute) -> IncidentNotificationRouteRecord:
    return IncidentNotificationRouteRecord(
        id=route.id,
        environment=route.environment,
        chat_id=route.chat_id,
        chat_name=route.chat_name,
        enabled=route.enabled,
        version=route.version,
        created_at=route.created_at,
        updated_at=route.updated_at,
    )


def _routes(uow: SqlAlchemyUnitOfWork) -> IncidentNotificationRouteRepository:
    if uow.incident_notification_routes is None:
        raise RuntimeError("工作单元没有可用飞书路由仓储")
    return uow.incident_notification_routes


def _operations(uow: SqlAlchemyUnitOfWork) -> IncidentNotificationRouteOperationRepository:
    if uow.incident_notification_route_operations is None:
        raise RuntimeError("工作单元没有可用飞书路由操作仓储")
    return uow.incident_notification_route_operations


__all__ = [
    "FeishuCredentialsIncomplete",
    "IncidentNotificationRouteConflict",
    "IncidentNotificationRouteIdempotencyConflict",
    "IncidentNotificationRouteMutationResult",
    "IncidentNotificationRouteNotFound",
    "IncidentNotificationRoutePage",
    "IncidentNotificationRouteService",
    "IncidentNotificationRouteVersionConflict",
]
