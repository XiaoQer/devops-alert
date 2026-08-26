from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy.exc import IntegrityError

from incident_intelligence.domain.alert_sources import (
    AlertSourceManagementType,
    AlertSourceState,
    AlertSourceType,
    CredentialState,
    ReceiptOutcome,
)
from incident_intelligence.domain.forbidden_identity import reject_forbidden_identity
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.alert_source_repository import AlertSourceRepository
from incident_intelligence.persistence.models import (
    AlertSourceCredentialRow,
    AlertSourceOperationRow,
    AlertSourceReceiptRow,
    AlertSourceRow,
)
from incident_intelligence.persistence.repositories import RecordRepositories
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork

SourceName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]


class CreateAlertSourceCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: SourceName
    source_type: Literal["ALERTMANAGER", "CLOUDEVENTS"]


class UpdateAlertSourceCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_version: int = Field(ge=1)
    name: SourceName | None = None
    state: AlertSourceState | None = None

    @model_validator(mode="after")
    def require_change(self) -> UpdateAlertSourceCommand:
        if self.name is None and self.state is None:
            raise ValueError("至少提供一个告警源变更字段")
        return self


class AlertSourceCredentialView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^acr_[0-9a-f]{32}$")
    state: CredentialState
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class AlertSourceView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^src_[0-9a-f]{32}$")
    name: SourceName
    source_type: AlertSourceType
    management_type: AlertSourceManagementType
    state: AlertSourceState
    version: int = Field(ge=1)
    last_accepted_at: datetime | None
    last_rejected_at: datetime | None
    last_validated_at: datetime | None
    accepted_requests: int = Field(ge=0)
    rejected_requests: int = Field(ge=0)
    opened_count: int = Field(ge=0)
    updated_count: int = Field(ge=0)
    resolved_count: int = Field(ge=0)
    replayed_count: int = Field(ge=0)
    ignored_count: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    credentials: tuple[AlertSourceCredentialView, ...]


class AlertSourcePage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[AlertSourceView, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=10_000)


class AlertSourceMutationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: AlertSourceView
    credential_id: str | None = Field(default=None, pattern=r"^acr_[0-9a-f]{32}$")
    token: str | None = None
    secret_retrievable: bool
    replayed: bool


class AlertSourceReceiptView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^rcp_[0-9a-f]{32}$")
    adapter_type: AlertSourceType
    outcome: ReceiptOutcome
    reason_code: str
    request_id: str
    input_count: int = Field(ge=0)
    opened_count: int = Field(ge=0)
    updated_count: int = Field(ge=0)
    resolved_count: int = Field(ge=0)
    replayed_count: int = Field(ge=0)
    ignored_count: int = Field(ge=0)
    received_at: datetime


class AlertSourceReceiptPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[AlertSourceReceiptView, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=10_000)


@dataclass(frozen=True, slots=True)
class AlertSourceConflict(Exception):
    reason_code: str = "alert_source_conflict"


@dataclass(frozen=True, slots=True)
class AlertSourceVersionConflict(Exception):
    reason_code: str = "alert_source_version_conflict"


@dataclass(frozen=True, slots=True)
class AlertSourceResourceNotFound(Exception):
    reason_code: str = "alert_source_not_found"


@dataclass(frozen=True, slots=True)
class SystemManagedSourceError(Exception):
    reason_code: str = "system_managed_source_read_only"


@dataclass(frozen=True, slots=True)
class LastActiveCredentialError(Exception):
    reason_code: str = "last_active_credential"


class AlertSourceService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[IdPrefix], str] = new_id,
        secret_factory: Callable[[], str] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory
        self._secret_factory = secret_factory or (lambda: token_urlsafe(32))

    def create_source(
        self,
        command: CreateAlertSourceCommand,
        *,
        idempotency_key: str,
        actor: str,
        request_id: str,
    ) -> AlertSourceMutationResult:
        reject_forbidden_identity(command.model_dump(mode="json"))
        scope = "src:create"
        key_hash, fingerprint = _operation_identity(scope, idempotency_key, command)
        try:
            with self._uow_factory() as uow:
                sources = _sources(uow)
                replay = self._find_replay(sources, scope, key_hash, fingerprint)
                if replay is not None:
                    return replay
                if sources.find_source_by_name(command.name) is not None:
                    raise AlertSourceConflict("alert_source_name_conflict")

                now = self._now()
                source = AlertSourceRow(
                    id=self._id_factory("src"),
                    name=command.name,
                    source_type=command.source_type,
                    management_type="USER_MANAGED",
                    state="ENABLED",
                    version=1,
                    last_accepted_at=None,
                    last_rejected_at=None,
                    last_validated_at=None,
                    accepted_requests=0,
                    rejected_requests=0,
                    opened_count=0,
                    updated_count=0,
                    resolved_count=0,
                    replayed_count=0,
                    ignored_count=0,
                    created_at=now,
                    updated_at=now,
                )
                sources.add_source(source)
                credential_id, token = self._new_credential(sources, source.id, actor, now)
                self._record_operation(
                    sources,
                    scope=scope,
                    key_hash=key_hash,
                    fingerprint=fingerprint,
                    action="CREATE",
                    source=source,
                    credential_id=credential_id,
                    now=now,
                )
                self._audit(
                    uow,
                    actor=actor,
                    action="alert_source.created",
                    source=source,
                    request_id=request_id,
                    reason_code="alert_source_created",
                    now=now,
                )
                result = self._mutation_result(
                    sources,
                    source,
                    credential_id=credential_id,
                    token=token,
                    replayed=False,
                )
                uow.commit()
                return result
        except IntegrityError as error:
            replay = self._replay_after_conflict(scope, key_hash, fingerprint)
            if replay is not None:
                return replay
            raise AlertSourceConflict("alert_source_name_conflict") from error

    def update_source(
        self,
        source_id: str,
        command: UpdateAlertSourceCommand,
        *,
        idempotency_key: str,
        actor: str,
        request_id: str,
    ) -> AlertSourceMutationResult:
        reject_forbidden_identity(command.model_dump(mode="json"))
        scope = f"src:update:{source_id}"
        key_hash, fingerprint = _operation_identity(scope, idempotency_key, command)
        try:
            with self._uow_factory() as uow:
                sources = _sources(uow)
                replay = self._find_replay(sources, scope, key_hash, fingerprint)
                if replay is not None:
                    return replay
                source = self._mutable_source(sources, source_id)
                replay = self._find_replay(sources, scope, key_hash, fingerprint)
                if replay is not None:
                    return replay
                self._require_version(source, command.expected_version)
                if command.name is not None:
                    duplicate = sources.find_source_by_name(command.name)
                    if duplicate is not None and duplicate.id != source.id:
                        raise AlertSourceConflict("alert_source_name_conflict")
                    source.name = command.name
                if command.state == "ENABLED" and sources.active_credential_count(source.id) == 0:
                    raise LastActiveCredentialError("enabled_source_requires_credential")
                if command.state is not None:
                    source.state = command.state
                now = self._advance(source)
                sources.flush()
                self._record_operation(
                    sources,
                    scope=scope,
                    key_hash=key_hash,
                    fingerprint=fingerprint,
                    action="UPDATE",
                    source=source,
                    credential_id=None,
                    now=now,
                )
                self._audit(
                    uow,
                    actor=actor,
                    action="alert_source.updated",
                    source=source,
                    request_id=request_id,
                    reason_code="alert_source_updated",
                    now=now,
                )
                result = self._mutation_result(sources, source, replayed=False)
                uow.commit()
                return result
        except IntegrityError as error:
            replay = self._replay_after_conflict(scope, key_hash, fingerprint)
            if replay is not None:
                return replay
            raise AlertSourceConflict("alert_source_name_conflict") from error

    def rotate_credential(
        self,
        source_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
        actor: str,
        request_id: str,
    ) -> AlertSourceMutationResult:
        payload = {"source_id": source_id, "expected_version": expected_version}
        scope = f"src:rotate:{source_id}"
        key_hash, fingerprint = _operation_identity(scope, idempotency_key, payload)
        try:
            with self._uow_factory() as uow:
                sources = _sources(uow)
                replay = self._find_replay(sources, scope, key_hash, fingerprint)
                if replay is not None:
                    return replay
                source = self._mutable_source(sources, source_id)
                replay = self._find_replay(sources, scope, key_hash, fingerprint)
                if replay is not None:
                    return replay
                self._require_version(source, expected_version)
                now = self._now()
                credential_id, token = self._new_credential(sources, source.id, actor, now)
                self._advance(source, now)
                sources.flush()
                self._record_operation(
                    sources,
                    scope=scope,
                    key_hash=key_hash,
                    fingerprint=fingerprint,
                    action="ROTATE",
                    source=source,
                    credential_id=credential_id,
                    now=now,
                )
                self._audit(
                    uow,
                    actor=actor,
                    action="alert_source.credential_rotated",
                    source=source,
                    request_id=request_id,
                    reason_code="credential_rotated",
                    now=now,
                )
                result = self._mutation_result(
                    sources,
                    source,
                    credential_id=credential_id,
                    token=token,
                    replayed=False,
                )
                uow.commit()
                return result
        except IntegrityError as error:
            replay = self._replay_after_conflict(scope, key_hash, fingerprint)
            if replay is not None:
                return replay
            raise AlertSourceConflict("credential_rotation_conflict") from error

    def revoke_credential(
        self,
        source_id: str,
        credential_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
        actor: str,
        request_id: str,
    ) -> AlertSourceMutationResult:
        payload = {
            "source_id": source_id,
            "credential_id": credential_id,
            "expected_version": expected_version,
        }
        scope = f"src:revoke:{source_id}"
        key_hash, fingerprint = _operation_identity(scope, idempotency_key, payload)
        with self._uow_factory() as uow:
            sources = _sources(uow)
            replay = self._find_replay(sources, scope, key_hash, fingerprint)
            if replay is not None:
                return replay
            source = self._mutable_source(sources, source_id)
            replay = self._find_replay(sources, scope, key_hash, fingerprint)
            if replay is not None:
                return replay
            self._require_version(source, expected_version)
            credential = sources.find_credential(source.id, credential_id, for_update=True)
            if credential is None:
                raise AlertSourceResourceNotFound("credential_not_found")
            if credential.state != "ACTIVE":
                raise AlertSourceConflict("credential_not_active")
            if source.state == "ENABLED" and sources.active_credential_count(source.id) <= 1:
                raise LastActiveCredentialError()

            now = self._now()
            credential.state = "REVOKED"
            credential.revoked_at = now
            credential.revoked_by = actor
            self._advance(source, now)
            sources.flush()
            self._record_operation(
                sources,
                scope=scope,
                key_hash=key_hash,
                fingerprint=fingerprint,
                action="REVOKE",
                source=source,
                credential_id=credential.id,
                now=now,
            )
            self._audit(
                uow,
                actor=actor,
                action="alert_source.credential_revoked",
                source=source,
                request_id=request_id,
                reason_code="credential_revoked",
                now=now,
            )
            result = self._mutation_result(
                sources,
                source,
                credential_id=credential.id,
                replayed=False,
            )
            uow.commit()
            return result

    def get_source(self, source_id: str) -> AlertSourceView:
        with self._uow_factory() as uow:
            sources = _sources(uow)
            source = sources.find_source(source_id)
            if source is None:
                raise AlertSourceResourceNotFound()
            return _source_view(sources, source)

    def list_sources(
        self,
        *,
        source_type: AlertSourceType | None = None,
        state: AlertSourceState | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> AlertSourcePage:
        with self._uow_factory() as uow:
            sources = _sources(uow)
            rows = sources.list_sources(
                source_type=source_type,
                state=state,
                limit=limit,
                offset=offset,
            )
            return AlertSourcePage(
                items=tuple(_source_view(sources, row) for row in rows),
                total=sources.count_sources(source_type=source_type, state=state),
                limit=limit,
                offset=offset,
            )

    def list_receipts(
        self,
        source_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        now: datetime | None = None,
    ) -> AlertSourceReceiptPage:
        cutoff = (now or self._now()).astimezone(UTC) - timedelta(days=30)
        with self._uow_factory() as uow:
            sources = _sources(uow)
            if sources.find_source(source_id) is None:
                raise AlertSourceResourceNotFound()
            rows = sources.list_receipts(
                source_id,
                since=cutoff,
                limit=limit,
                offset=offset,
            )
            return AlertSourceReceiptPage(
                items=tuple(_receipt_view(row) for row in rows),
                total=sources.count_receipts(source_id, since=cutoff),
                limit=limit,
                offset=offset,
            )

    def _new_credential(
        self,
        sources: AlertSourceRepository,
        source_id: str,
        actor: str,
        now: datetime,
    ) -> tuple[str, str]:
        credential_id = self._id_factory("acr")
        token = f"iisrc_{credential_id}.{self._secret_factory()}"
        sources.add_credential(
            AlertSourceCredentialRow(
                id=credential_id,
                alert_source_id=source_id,
                token_digest=sha256(token.encode("utf-8")).hexdigest(),
                state="ACTIVE",
                created_by=actor,
                last_used_at=None,
                revoked_at=None,
                revoked_by=None,
                created_at=now,
            )
        )
        return credential_id, token

    def _mutable_source(
        self,
        sources: AlertSourceRepository,
        source_id: str,
    ) -> AlertSourceRow:
        source = sources.find_source(source_id, for_update=True)
        if source is None:
            raise AlertSourceResourceNotFound()
        if source.management_type == "SYSTEM_MANAGED":
            raise SystemManagedSourceError()
        return source

    @staticmethod
    def _require_version(source: AlertSourceRow, expected_version: int) -> None:
        if source.version != expected_version:
            raise AlertSourceVersionConflict()

    def _advance(self, source: AlertSourceRow, now: datetime | None = None) -> datetime:
        changed_at = now or self._now()
        source.version += 1
        source.updated_at = changed_at
        return changed_at

    def _record_operation(
        self,
        sources: AlertSourceRepository,
        *,
        scope: str,
        key_hash: str,
        fingerprint: str,
        action: str,
        source: AlertSourceRow,
        credential_id: str | None,
        now: datetime,
    ) -> None:
        sources.add_operation(
            AlertSourceOperationRow(
                id=self._id_factory("aso"),
                scope=scope,
                idempotency_key_hash=key_hash,
                command_fingerprint=fingerprint,
                action=action,
                alert_source_id=source.id,
                credential_id=credential_id,
                result_version=source.version,
                completed_at=now,
            )
        )

    def _find_replay(
        self,
        sources: AlertSourceRepository,
        scope: str,
        key_hash: str,
        fingerprint: str,
    ) -> AlertSourceMutationResult | None:
        operation = sources.find_operation(scope, key_hash)
        if operation is None:
            return None
        if operation.command_fingerprint != fingerprint:
            raise AlertSourceConflict("idempotency_conflict")
        source = sources.find_source(operation.alert_source_id)
        if source is None:
            raise RuntimeError("告警源操作引用的来源不存在")
        return self._mutation_result(
            sources,
            source,
            credential_id=operation.credential_id,
            replayed=True,
        )

    def _replay_after_conflict(
        self,
        scope: str,
        key_hash: str,
        fingerprint: str,
    ) -> AlertSourceMutationResult | None:
        with self._uow_factory() as uow:
            return self._find_replay(_sources(uow), scope, key_hash, fingerprint)

    @staticmethod
    def _mutation_result(
        sources: AlertSourceRepository,
        source: AlertSourceRow,
        *,
        credential_id: str | None = None,
        token: str | None = None,
        replayed: bool,
    ) -> AlertSourceMutationResult:
        return AlertSourceMutationResult(
            source=_source_view(sources, source),
            credential_id=credential_id,
            token=token,
            secret_retrievable=token is not None,
            replayed=replayed,
        )

    def _audit(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        actor: str,
        action: str,
        source: AlertSourceRow,
        request_id: str,
        reason_code: str,
        now: datetime,
    ) -> None:
        _records(uow).add_audit(
            audit_id=self._id_factory("aud"),
            actor=actor,
            action=action,
            resource_type="alert_source",
            resource_id=source.id,
            request_id=request_id,
            details={
                "reason_code": reason_code,
                "new_state": source.state,
                "new_version": str(source.version),
            },
            created_at=now,
        )

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
    key_hash = sha256(normalized_key.encode("utf-8")).hexdigest()
    fingerprint = sha256(f"{scope}:{canonical}".encode()).hexdigest()
    return key_hash, fingerprint


def _source_view(sources: AlertSourceRepository, source: AlertSourceRow) -> AlertSourceView:
    credentials = tuple(
        AlertSourceCredentialView(
            id=row.id,
            state=cast(CredentialState, row.state),
            last_used_at=row.last_used_at,
            revoked_at=row.revoked_at,
            created_at=row.created_at,
        )
        for row in sources.list_credentials(source.id)
    )
    return AlertSourceView(
        id=source.id,
        name=source.name,
        source_type=cast(AlertSourceType, source.source_type),
        management_type=cast(AlertSourceManagementType, source.management_type),
        state=cast(AlertSourceState, source.state),
        version=source.version,
        last_accepted_at=source.last_accepted_at,
        last_rejected_at=source.last_rejected_at,
        last_validated_at=source.last_validated_at,
        accepted_requests=source.accepted_requests,
        rejected_requests=source.rejected_requests,
        opened_count=source.opened_count,
        updated_count=source.updated_count,
        resolved_count=source.resolved_count,
        replayed_count=source.replayed_count,
        ignored_count=source.ignored_count,
        created_at=source.created_at,
        updated_at=source.updated_at,
        credentials=credentials,
    )


def _receipt_view(row: AlertSourceReceiptRow) -> AlertSourceReceiptView:
    return AlertSourceReceiptView(
        id=row.id,
        adapter_type=cast(AlertSourceType, row.adapter_type),
        outcome=cast(ReceiptOutcome, row.outcome),
        reason_code=row.reason_code,
        request_id=row.request_id,
        input_count=row.input_count,
        opened_count=row.opened_count,
        updated_count=row.updated_count,
        resolved_count=row.resolved_count,
        replayed_count=row.replayed_count,
        ignored_count=row.ignored_count,
        received_at=row.received_at,
    )


def _sources(uow: SqlAlchemyUnitOfWork) -> AlertSourceRepository:
    if uow.alert_sources is None:
        raise RuntimeError("工作单元没有可用告警源仓储")
    return uow.alert_sources


def _records(uow: SqlAlchemyUnitOfWork) -> RecordRepositories:
    if uow.records is None:
        raise RuntimeError("工作单元没有可用记录仓储")
    return uow.records
