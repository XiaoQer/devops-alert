from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_core import PydanticCustomError

from incident_intelligence.domain.forbidden_identity import (
    ForbiddenIdentityError,
    reject_forbidden_identity,
)

SourceName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
EnvironmentCode = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,31}$", max_length=32),
]
EnvironmentName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]
SourceId = Annotated[str, StringConstraints(pattern=r"^src_[0-9a-f]{32}$")]
CredentialId = Annotated[str, StringConstraints(pattern=r"^acr_[0-9a-f]{32}$")]


class AlertSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def reject_experiment_identity(cls, value: object) -> object:
        try:
            reject_forbidden_identity(value)
        except ForbiddenIdentityError as error:
            raise PydanticCustomError("forbidden_identity", "forbidden identity") from error
        return value


class CreateAlertSourceRequest(AlertSourceRequest):
    name: SourceName
    source_type: Literal["ALERTMANAGER", "CLOUDEVENTS"]
    environment: EnvironmentCode
    environment_name: EnvironmentName


class UpdateAlertSourceRequest(AlertSourceRequest):
    expected_version: int = Field(ge=1)
    name: SourceName | None = None
    state: Literal["ENABLED", "DISABLED"] | None = None
    environment: EnvironmentCode | None = None
    environment_name: EnvironmentName | None = None

    @model_validator(mode="after")
    def require_complete_environment(self) -> UpdateAlertSourceRequest:
        if (self.environment is None) != (self.environment_name is None):
            raise ValueError("环境代码和环境名称必须同时提供")
        return self


class CredentialMutationRequest(AlertSourceRequest):
    expected_version: int = Field(ge=1)


class AlertSourceCredentialResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    id: CredentialId
    state: Literal["ACTIVE", "REVOKED"]
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class AlertSourceResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    id: SourceId
    name: SourceName
    source_type: Literal["ALERTMANAGER", "CLOUDEVENTS", "MANUAL"]
    management_type: Literal["USER_MANAGED", "SYSTEM_MANAGED"]
    state: Literal["ENABLED", "DISABLED"]
    environment: EnvironmentCode
    environment_name: EnvironmentName
    environment_configured: bool
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
    credentials: tuple[AlertSourceCredentialResponse, ...]


class AlertSourceMutationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    source: AlertSourceResponse
    credential_id: CredentialId | None
    token: str | None
    secret_retrievable: bool
    replayed: bool


class AlertSourcePageResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    items: tuple[AlertSourceResponse, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=10_000)


class AlertSourceReceiptResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    id: Annotated[str, StringConstraints(pattern=r"^rcp_[0-9a-f]{32}$")]
    adapter_type: Literal["ALERTMANAGER", "CLOUDEVENTS", "MANUAL"]
    outcome: Literal[
        "ACCEPTED",
        "REPLAYED",
        "VALIDATED",
        "PAYLOAD_REJECTED",
        "SOURCE_DISABLED",
        "PROCESSING_FAILED",
    ]
    reason_code: str
    request_id: str
    input_count: int = Field(ge=0)
    opened_count: int = Field(ge=0)
    updated_count: int = Field(ge=0)
    resolved_count: int = Field(ge=0)
    replayed_count: int = Field(ge=0)
    ignored_count: int = Field(ge=0)
    received_at: datetime


class AlertSourceReceiptPageResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    items: tuple[AlertSourceReceiptResponse, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=10_000)
