from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_core import PydanticCustomError

from incident_intelligence.domain.forbidden_identity import (
    ForbiddenIdentityError,
    reject_forbidden_identity,
)

ServiceName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
OwnerTeam = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
ServiceId = Annotated[str, StringConstraints(pattern=r"^svc_[0-9a-f]{32}$")]
DependencyId = Annotated[str, StringConstraints(pattern=r"^dep_[0-9a-f]{32}$")]
Environment = Literal["production", "staging", "development", "unknown"]


class CatalogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def reject_experiment_identity(cls, value: object) -> object:
        try:
            reject_forbidden_identity(value)
        except ForbiddenIdentityError as error:
            raise PydanticCustomError("forbidden_identity", "forbidden identity") from error
        return value


class CreateServiceRequest(CatalogRequest):
    service: ServiceName
    environment: Environment
    owner_team: OwnerTeam


class UpdateServiceRequest(CatalogRequest):
    expected_version: int = Field(ge=1)
    owner_team: OwnerTeam | None = None
    state: Literal["ACTIVE", "INACTIVE"] | None = None


class CreateDependencyRequest(CatalogRequest):
    caller_service_id: ServiceId
    dependency_service_id: ServiceId


class UpdateDependencyRequest(CatalogRequest):
    expected_version: int = Field(ge=1)
    state: Literal["ACTIVE", "INACTIVE"]


class ServiceCatalogResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    id: ServiceId
    service: ServiceName
    environment: Environment
    owner_team: OwnerTeam
    state: Literal["ACTIVE", "INACTIVE"]
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class ServiceCatalogListResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[ServiceCatalogResponse, ...]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=10_000)


class ServiceDependencyResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    id: DependencyId
    caller_service_id: ServiceId
    dependency_service_id: ServiceId
    state: Literal["ACTIVE", "INACTIVE"]
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class ServiceDependencyListResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[ServiceDependencyResponse, ...]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=10_000)
