from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from incident_intelligence.domain.enums import CatalogState, DependencyState
from incident_intelligence.domain.models import Environment, ServiceName, UtcAwareDatetime

Symptom = Literal[
    "error_rate",
    "latency",
    "cpu_saturation",
    "memory_pressure",
    "availability",
]
OwnerTeam = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]

_SYMPTOM_ALIASES: dict[str, Symptom] = {
    "high-error-rate": "error_rate",
    "error-rate": "error_rate",
    "errors": "error_rate",
    "latency": "latency",
    "slow": "latency",
    "cpu": "cpu_saturation",
    "high-cpu": "cpu_saturation",
    "oom": "memory_pressure",
    "memory": "memory_pressure",
    "down": "availability",
    "unavailable": "availability",
}


def normalize_symptom(value: str | None) -> Symptom | None:
    if value is None:
        return None
    return _SYMPTOM_ALIASES.get(value.strip().casefold())


class CatalogDomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    created_at: UtcAwareDatetime
    updated_at: UtcAwareDatetime
    version: int = Field(default=1, ge=1)


class ServiceCatalogEntry(CatalogDomainModel):
    id: str = Field(pattern=r"^svc_[0-9a-f]{32}$")
    service: ServiceName
    environment: Environment
    owner_team: OwnerTeam
    state: CatalogState


class ServiceDependency(CatalogDomainModel):
    id: str = Field(pattern=r"^dep_[0-9a-f]{32}$")
    caller_service_id: str = Field(pattern=r"^svc_[0-9a-f]{32}$")
    dependency_service_id: str = Field(pattern=r"^svc_[0-9a-f]{32}$")
    state: DependencyState
