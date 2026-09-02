from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from sqlalchemy.exc import IntegrityError

from incident_intelligence.domain.models import Environment, UtcAwareDatetime
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.evidence_repository import (
    MonitoringDataSourceRecord,
    MonitoringDataSourceRepository,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork

MonitoringSourceType = Literal["PROMETHEUS", "ELASTICSEARCH", "SKYWALKING"]
ConnectionState = Literal["AVAILABLE", "UNAVAILABLE"]
ALLOWED_FIELD_KEYS: dict[MonitoringSourceType, frozenset[str]] = {
    "PROMETHEUS": frozenset({"service", "environment"}),
    "ELASTICSEARCH": frozenset(
        {
            "index",
            "timestamp",
            "service",
            "environment",
            "level",
            "message",
            "error_type",
            "trace_id",
            "host",
        }
    ),
    "SKYWALKING": frozenset({"graphql_path"}),
}


class MonitoringDataSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^mds_[0-9a-f]{32}$")
    name: str = Field(min_length=1, max_length=128)
    environment: Environment
    source_type: MonitoringSourceType
    base_url: str = Field(min_length=1, max_length=2_000)
    credential_env_key: str | None = Field(
        default=None,
        pattern=r"^II_[A-Z0-9_]{1,120}$",
    )
    field_mapping: dict[str, str] = Field(default_factory=dict, max_length=16)
    verify_tls: bool
    enabled: bool
    version: int = Field(ge=1)
    last_test_state: ConnectionState | None = None
    last_test_latency_ms: int | None = Field(default=None, ge=0, le=120_000)
    last_compatible_version: str | None = Field(default=None, max_length=64)
    last_test_error_code: str | None = Field(default=None, max_length=64)
    last_tested_at: UtcAwareDatetime | None = None
    created_at: UtcAwareDatetime
    updated_at: UtcAwareDatetime

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("monitoring_base_url_unsafe")
        return normalized

    @model_validator(mode="after")
    def validate_field_mapping(self) -> MonitoringDataSource:
        unexpected = set(self.field_mapping) - ALLOWED_FIELD_KEYS[self.source_type]
        if unexpected:
            raise ValueError("monitoring_field_mapping_unsupported")
        if any(not 1 <= len(value.strip()) <= 256 for value in self.field_mapping.values()):
            raise ValueError("monitoring_field_mapping_value_invalid")
        return self


class CredentialCapability(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    configured: bool
    missing_environment_keys: tuple[str, ...]


class MonitoringDataSourceView(MonitoringDataSource):
    credential_configured: bool


class MonitoringDataSourcePage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[MonitoringDataSourceView, ...]
    total: int


class ConnectionTestResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: ConnectionState
    latency_ms: int | None = Field(default=None, ge=0, le=120_000)
    compatible_version: str | None = Field(default=None, max_length=64)
    error_code: str | None = Field(default=None, max_length=64)


class MonitoringConnectionTester(Protocol):
    def test(
        self,
        source: MonitoringDataSource,
        *,
        credential: str | None,
    ) -> ConnectionTestResult: ...


class _DeferredConnectionTester:
    def test(
        self,
        source: MonitoringDataSource,
        *,
        credential: str | None,
    ) -> ConnectionTestResult:
        del source, credential
        return ConnectionTestResult(
            state="UNAVAILABLE",
            error_code="monitoring_adapter_not_ready",
        )


@dataclass(frozen=True, slots=True)
class MonitoringDataSourceNotFound(Exception):
    reason_code: str = "monitoring_data_source_not_found"


@dataclass(frozen=True, slots=True)
class MonitoringDataSourceConflict(Exception):
    reason_code: str = "monitoring_data_source_conflict"


@dataclass(frozen=True, slots=True)
class MonitoringDataSourceVersionConflict(Exception):
    reason_code: str = "monitoring_data_source_version_conflict"


class MonitoringCredentialResolver:
    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ

    def resolve(self, source: MonitoringDataSource) -> str | None:
        if source.credential_env_key is None:
            return None
        value = self._environ.get(source.credential_env_key, "").strip()
        return value or None

    def capability(self, source: MonitoringDataSource) -> CredentialCapability:
        if source.credential_env_key is None:
            return CredentialCapability(configured=True, missing_environment_keys=())
        configured = self.resolve(source) is not None
        return CredentialCapability(
            configured=configured,
            missing_environment_keys=() if configured else (source.credential_env_key,),
        )


class MonitoringDataSourceService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        credential_resolver: MonitoringCredentialResolver | None = None,
        connection_tester: MonitoringConnectionTester | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[IdPrefix], str] = new_id,
    ) -> None:
        self._uow_factory = uow_factory
        self._credential_resolver = credential_resolver or MonitoringCredentialResolver()
        self._connection_tester = connection_tester or _DeferredConnectionTester()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory

    def list(self) -> MonitoringDataSourcePage:
        with self._uow_factory() as uow:
            items = tuple(self._view(_to_domain(item)) for item in _sources(uow).list_all())
        return MonitoringDataSourcePage(items=items, total=len(items))

    def create(
        self,
        *,
        name: str,
        environment: str,
        source_type: MonitoringSourceType,
        base_url: str,
        credential_env_key: str | None,
        field_mapping: dict[str, str],
        verify_tls: bool,
        enabled: bool,
    ) -> MonitoringDataSourceView:
        now = self._clock().astimezone(UTC)
        source = MonitoringDataSource(
            id=self._id_factory("mds"),
            name=name,
            environment=environment,
            source_type=source_type,
            base_url=base_url,
            credential_env_key=credential_env_key,
            field_mapping=field_mapping,
            verify_tls=verify_tls,
            enabled=enabled,
            version=1,
            created_at=now,
            updated_at=now,
        )
        try:
            with self._uow_factory() as uow:
                _sources(uow).insert(_to_record(source))
                uow.commit()
        except IntegrityError:
            raise MonitoringDataSourceConflict() from None
        return self._view(source)

    def update(
        self,
        source_id: str,
        *,
        expected_version: int,
        name: str,
        environment: str,
        source_type: MonitoringSourceType,
        base_url: str,
        credential_env_key: str | None,
        field_mapping: dict[str, str],
        verify_tls: bool,
        enabled: bool,
    ) -> MonitoringDataSourceView:
        try:
            with self._uow_factory() as uow:
                repository = _sources(uow)
                current_record = repository.get(source_id, for_update=True)
                if current_record is None:
                    raise MonitoringDataSourceNotFound()
                if current_record.version != expected_version:
                    raise MonitoringDataSourceVersionConflict()
                current = _to_domain(current_record)
                updated = current.model_copy(
                    update={
                        "name": name,
                        "environment": environment,
                        "source_type": source_type,
                        "base_url": base_url,
                        "credential_env_key": credential_env_key,
                        "field_mapping": field_mapping,
                        "verify_tls": verify_tls,
                        "enabled": enabled,
                        "version": expected_version + 1,
                        "updated_at": self._clock().astimezone(UTC),
                    }
                )
                updated = MonitoringDataSource.model_validate(updated.model_dump())
                if not repository.update(_to_record(updated), expected_version=expected_version):
                    raise MonitoringDataSourceVersionConflict()
                uow.commit()
        except IntegrityError:
            raise MonitoringDataSourceConflict() from None
        return self._view(updated)

    def test_connection(self, source_id: str) -> ConnectionTestResult:
        with self._uow_factory() as uow:
            record = _sources(uow).get(source_id)
            if record is None:
                raise MonitoringDataSourceNotFound()
            source = _to_domain(record)
        return self._connection_tester.test(
            source,
            credential=self._credential_resolver.resolve(source),
        )

    def _view(self, source: MonitoringDataSource) -> MonitoringDataSourceView:
        return MonitoringDataSourceView(
            **source.model_dump(),
            credential_configured=self._credential_resolver.capability(source).configured,
        )


def _sources(uow: SqlAlchemyUnitOfWork) -> MonitoringDataSourceRepository:
    if uow.monitoring_data_sources is None:
        raise RuntimeError("监控数据源仓储尚未初始化")
    return uow.monitoring_data_sources


def _to_record(source: MonitoringDataSource) -> MonitoringDataSourceRecord:
    return MonitoringDataSourceRecord(**source.model_dump())


def _to_domain(record: MonitoringDataSourceRecord) -> MonitoringDataSource:
    return MonitoringDataSource.model_validate(record.__dict__)
