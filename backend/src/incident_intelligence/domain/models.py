from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from incident_intelligence.domain.entities import (
    EntityType,
    derive_entity_identity,
)
from incident_intelligence.domain.enums import AlertState, DiagnosisState, IncidentState

Severity = Literal["critical", "high", "medium", "low"]
Environment = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,31}$", max_length=32),
]
EventType = Literal["manual.reported", "alert.firing", "alert.resolved"]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Summary = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000)]
ServiceName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
FactKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
FactValue = Annotated[str, StringConstraints(strip_whitespace=True, max_length=512)]


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcAwareDatetime = Annotated[AwareDatetime, AfterValidator(_as_utc)]


class FrozenDomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    created_at: UtcAwareDatetime
    version: int = Field(default=1, ge=1)


class SignalEvent(FrozenDomainModel):
    id: str = Field(pattern=r"^sig_[0-9a-f]{32}$")
    alert_source_id: str = Field(pattern=r"^src_[0-9a-f]{32}$")
    source: str = Field(min_length=1, max_length=64)
    source_event_id: str = Field(min_length=1, max_length=256)
    event_type: EventType
    title: Title
    summary: Summary
    severity: Severity
    service: ServiceName | None
    entity_type: EntityType
    entity_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    entity_display_name: str = Field(min_length=1, max_length=257)
    environment: Environment
    observed_at: UtcAwareDatetime
    received_at: UtcAwareDatetime
    facts: dict[FactKey, FactValue] = Field(default_factory=dict, max_length=50)
    payload_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def derive_missing_entity(cls, value: object) -> object:
        return _with_derived_service_entity(value)


class Alert(FrozenDomainModel):
    id: str = Field(pattern=r"^alt_[0-9a-f]{32}$")
    signal_event_id: str = Field(pattern=r"^sig_[0-9a-f]{32}$")
    alert_source_id: str = Field(pattern=r"^src_[0-9a-f]{32}$")
    source: str = Field(min_length=1, max_length=64)
    source_instance: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_alert_key: str = Field(min_length=1, max_length=128)
    state: AlertState
    cycle: int = Field(default=1, ge=1)
    title: Title
    severity: Severity
    service: ServiceName | None
    entity_type: EntityType
    entity_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    entity_display_name: str = Field(min_length=1, max_length=257)
    environment: Environment
    first_observed_at: UtcAwareDatetime
    last_observed_at: UtcAwareDatetime
    state_changed_at: UtcAwareDatetime

    @model_validator(mode="before")
    @classmethod
    def derive_missing_entity(cls, value: object) -> object:
        return _with_derived_service_entity(value)


def _with_derived_service_entity(value: object) -> object:
    if not isinstance(value, dict) or "entity_type" in value:
        return value
    service = value.get("service")
    if not isinstance(service, str) or not service.strip():
        return value
    identity = derive_entity_identity({"service": service})
    return {
        **value,
        "entity_type": identity.entity_type,
        "entity_key": identity.entity_key,
        "entity_display_name": identity.display_name,
    }


class Incident(FrozenDomainModel):
    id: str = Field(pattern=r"^inc_[0-9a-f]{32}$")
    primary_alert_id: str = Field(pattern=r"^alt_[0-9a-f]{32}$")
    state: IncidentState
    title: Title
    severity: Severity
    service: ServiceName
    environment: Environment
    detected_at: UtcAwareDatetime


class DiagnosisRun(FrozenDomainModel):
    id: str = Field(pattern=r"^diag_[0-9a-f]{32}$")
    incident_id: str = Field(pattern=r"^inc_[0-9a-f]{32}$")
    incident_context_version: int = Field(ge=1)
    state: DiagnosisState
