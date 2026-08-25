from __future__ import annotations

import json
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from incident_intelligence.adapters.common import (
    AdapterValidationError,
    normalize_source_uri,
)
from incident_intelligence.domain.forbidden_identity import reject_forbidden_identity
from incident_intelligence.domain.models import UtcAwareDatetime
from incident_intelligence.domain.signal_intake import SignalCommand

BoundedKey = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]
BoundedValue = Annotated[str, StringConstraints(strip_whitespace=True, max_length=512)]
BoundedText = Annotated[str, StringConstraints(max_length=2_048)]

FACT_LABELS = frozenset(
    {
        "region",
        "cluster",
        "namespace",
        "pod",
        "instance",
        "job",
        "team",
        "component",
        "node",
        "container",
        "symptom",
    }
)
SEVERITY_ALIASES = {
    "critical": "critical",
    "page": "critical",
    "p1": "critical",
    "high": "high",
    "p2": "high",
    "warning": "medium",
    "warn": "medium",
    "medium": "medium",
    "p3": "medium",
    "info": "low",
    "low": "low",
    "p4": "low",
    "p5": "low",
}


class AlertmanagerAlert(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    status: Literal["firing", "resolved"]
    labels: dict[BoundedKey, BoundedValue] = Field(max_length=100)
    annotations: dict[BoundedKey, BoundedValue] = Field(default_factory=dict, max_length=20)
    starts_at: UtcAwareDatetime = Field(alias="startsAt")
    ends_at: UtcAwareDatetime = Field(alias="endsAt")
    generator_url: BoundedText = Field(alias="generatorURL")
    fingerprint: str = Field(min_length=1, max_length=128)


class AlertmanagerWebhook(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: Literal["4"]
    group_key: BoundedText = Field(alias="groupKey")
    truncated_alerts: int = Field(alias="truncatedAlerts", ge=0)
    status: Literal["firing", "resolved"]
    receiver: str = Field(min_length=1, max_length=256)
    group_labels: dict[BoundedKey, BoundedValue] = Field(alias="groupLabels", max_length=100)
    common_labels: dict[BoundedKey, BoundedValue] = Field(alias="commonLabels", max_length=100)
    common_annotations: dict[BoundedKey, BoundedValue] = Field(
        alias="commonAnnotations", max_length=20
    )
    external_url: BoundedText = Field(alias="externalURL")
    alerts: tuple[AlertmanagerAlert, ...] = Field(min_length=1, max_length=100)


def to_signal_commands(
    webhook: AlertmanagerWebhook,
    now: datetime,
) -> tuple[SignalCommand, ...]:
    reject_forbidden_identity(webhook.model_dump(mode="json"))
    normalized_uri = normalize_source_uri(webhook.external_url)
    source_instance = sha256(normalized_uri.encode("utf-8")).hexdigest()
    return tuple(_to_signal_command(source_instance, alert, now) for alert in webhook.alerts)


def _to_signal_command(
    source_instance: str,
    alert: AlertmanagerAlert,
    now: datetime,
) -> SignalCommand:
    title = alert.annotations.get("summary") or alert.labels.get("alertname")
    if title is None:
        raise AdapterValidationError("missing_title")
    service = alert.labels.get("service")
    if service is None:
        raise AdapterValidationError("missing_service")

    event_at = alert.starts_at if alert.status == "firing" else alert.ends_at
    if event_at > now + timedelta(minutes=5):
        raise AdapterValidationError("event_time_in_future")
    if alert.status == "resolved" and alert.ends_at.year == 1:
        raise AdapterValidationError("missing_resolved_time")

    severity_value = alert.labels.get("severity", "").casefold()
    severity = SEVERITY_ALIASES.get(severity_value, "medium")
    reason_codes = () if severity_value in SEVERITY_ALIASES else ("severity_defaulted",)
    environment = alert.labels.get("environment", "unknown")
    facts = {key: value for key, value in alert.labels.items() if key in FACT_LABELS}
    summary = alert.annotations.get("description") or title
    event_type = "alert.firing" if alert.status == "firing" else "alert.resolved"
    command_content = {
        "event_type": event_type,
        "event_at": event_at.isoformat(),
        "episode_started_at": alert.starts_at.isoformat(),
        "title": title,
        "summary": summary,
        "severity": severity,
        "service": service,
        "environment": environment,
        "facts": facts,
        "normalization_reason_codes": reason_codes,
    }
    identity = {
        "source_instance": source_instance,
        "fingerprint": alert.fingerprint,
        **command_content,
    }
    source_event_id = sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return SignalCommand.model_validate(
        {
            **command_content,
            "source": "alertmanager",
            "source_instance": source_instance,
            "source_event_id": source_event_id,
            "source_alert_key": alert.fingerprint,
        }
    )
