from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from incident_intelligence.adapters.common import (
    AdapterValidationError,
    normalize_source_uri,
)
from incident_intelligence.domain.forbidden_identity import reject_forbidden_identity
from incident_intelligence.domain.models import (
    Environment,
    FactKey,
    FactValue,
    ServiceName,
    Severity,
    Summary,
    Title,
    UtcAwareDatetime,
)
from incident_intelligence.domain.signal_intake import SignalCommand

CloudEventId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]
CloudEventSource = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_048),
]
AlertKey = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]

EVENT_TYPE = "com.incidentintelligence.alert.v1"
ALLOWED_BINARY_ATTRIBUTES = frozenset(
    {
        "ce-specversion",
        "ce-id",
        "ce-source",
        "ce-type",
        "ce-subject",
        "ce-time",
    }
)


class CloudEventData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    alert_key: AlertKey
    title: Title
    summary: Summary
    severity: Severity
    service: ServiceName
    environment: Environment
    status: Literal["firing", "resolved"]
    started_at: UtcAwareDatetime
    labels: dict[FactKey, FactValue] = Field(default_factory=dict, max_length=20)


class BinaryCloudEventContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    specversion: Literal["1.0"]
    id: CloudEventId
    source: CloudEventSource
    type: Literal["com.incidentintelligence.alert.v1"]
    subject: ServiceName | None = None
    time: UtcAwareDatetime
    datacontenttype: Literal["application/json"]

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> BinaryCloudEventContext:
        normalized = {key.casefold(): value for key, value in headers.items()}
        extensions = {
            key
            for key in normalized
            if key.startswith("ce-") and key not in ALLOWED_BINARY_ATTRIBUTES
        }
        if extensions:
            raise AdapterValidationError("unsupported_extension_attribute")
        content_type = normalized.get("content-type", "").partition(";")[0].strip().casefold()
        return cls.model_validate(
            {
                "specversion": normalized.get("ce-specversion"),
                "id": normalized.get("ce-id"),
                "source": normalized.get("ce-source"),
                "type": normalized.get("ce-type"),
                "subject": normalized.get("ce-subject"),
                "time": normalized.get("ce-time"),
                "datacontenttype": content_type,
            }
        )


class StructuredCloudEvent(BinaryCloudEventContext):
    model_config = ConfigDict(frozen=True, extra="forbid")

    data: CloudEventData

    def context(self) -> BinaryCloudEventContext:
        return BinaryCloudEventContext.model_validate(self.model_dump(exclude={"data"}))


def structured_to_signal_command(
    event: StructuredCloudEvent,
    now: datetime,
) -> SignalCommand:
    return binary_to_signal_command(event.context(), event.data, now)


def binary_to_signal_command(
    context: BinaryCloudEventContext,
    data: CloudEventData,
    now: datetime,
) -> SignalCommand:
    reject_forbidden_identity(context.model_dump(mode="json"))
    reject_forbidden_identity(data.model_dump(mode="json"))
    if context.subject is not None and context.subject != data.service:
        raise AdapterValidationError("subject_service_mismatch")
    if context.time > now + timedelta(minutes=5):
        raise AdapterValidationError("event_time_in_future")

    normalized_source = normalize_source_uri(context.source)
    source_instance = sha256(normalized_source.encode("utf-8")).hexdigest()
    source_event_id = sha256(
        normalized_source.encode("utf-8") + b"\0" + context.id.encode("utf-8")
    ).hexdigest()
    return SignalCommand.model_validate(
        {
            "source": "cloudevents",
            "source_instance": source_instance,
            "source_event_id": source_event_id,
            "source_alert_key": data.alert_key,
            "event_type": f"alert.{data.status}",
            "event_at": context.time,
            "episode_started_at": data.started_at,
            "title": data.title,
            "summary": data.summary,
            "severity": data.severity,
            "service": data.service,
            "environment": data.environment,
            "facts": data.labels,
            "normalization_reason_codes": (),
        }
    )
