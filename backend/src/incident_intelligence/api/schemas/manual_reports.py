from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from incident_intelligence.domain.forbidden_identity import (
    ForbiddenIdentityError,
    reject_forbidden_identity,
)

BoundedTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
BoundedSummary = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000)
]
BoundedService = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]
LabelKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
LabelValue = Annotated[str, StringConstraints(strip_whitespace=True, max_length=256)]


class ManualReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: BoundedTitle
    summary: BoundedSummary
    severity: Literal["critical", "high", "medium", "low"]
    service: BoundedService
    environment: Literal["production", "staging", "development", "unknown"]
    observed_at: AwareDatetime
    labels: dict[LabelKey, LabelValue] = Field(default_factory=dict, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def reject_experiment_identity(cls, value: object) -> object:
        try:
            reject_forbidden_identity(value)
        except ForbiddenIdentityError as error:
            raise PydanticCustomError("forbidden_identity", "forbidden identity") from error
        return value

    @field_validator("observed_at")
    @classmethod
    def reject_far_future_time(cls, value: datetime) -> datetime:
        if value > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError("observed_at exceeds future tolerance")
        return value.astimezone(UTC)


class ManualReportResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_event_id: str
    alert_id: str
    incident_id: str
    diagnosis_run_id: str
    replayed: bool
