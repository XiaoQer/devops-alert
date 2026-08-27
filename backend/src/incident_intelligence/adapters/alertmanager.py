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
from incident_intelligence.domain.alert_sources import ALERTMANAGER_COMPAT_SOURCE_ID
from incident_intelligence.domain.entities import derive_entity_identity
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
        "alertname",
        "region",
        "cluster",
        "namespace",
        "workload",
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
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    status: Literal["firing", "resolved"]
    labels: dict[BoundedKey, BoundedValue] = Field(max_length=100)
    annotations: dict[BoundedKey, BoundedValue] = Field(default_factory=dict, max_length=20)
    starts_at: UtcAwareDatetime = Field(alias="startsAt")
    ends_at: UtcAwareDatetime = Field(alias="endsAt")
    generator_url: BoundedText = Field(alias="generatorURL")
    fingerprint: str = Field(min_length=1, max_length=128)


class AlertmanagerWebhook(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

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
    *,
    alert_source_id: str = ALERTMANAGER_COMPAT_SOURCE_ID,
) -> tuple[SignalCommand, ...]:
    normalized_uri = normalize_source_uri(webhook.external_url)
    source_instance = sha256(normalized_uri.encode("utf-8")).hexdigest()
    return tuple(
        _to_signal_command(
            alert_source_id,
            source_instance,
            alert,
            {**webhook.common_labels, **alert.labels},
            now,
        )
        for alert in webhook.alerts
    )


def _to_signal_command(
    alert_source_id: str,
    source_instance: str,
    alert: AlertmanagerAlert,
    labels: dict[str, str],
    now: datetime,
) -> SignalCommand:
    title = alert.annotations.get("summary") or labels.get("alertname") or "未命名告警"
    identity = derive_entity_identity(labels)

    event_at = alert.starts_at if alert.status == "firing" else alert.ends_at
    if event_at > now + timedelta(minutes=5):
        raise AdapterValidationError("event_time_in_future")
    if alert.status == "resolved" and alert.ends_at.year == 1:
        raise AdapterValidationError("missing_resolved_time")

    severity_value = labels.get("severity", "").casefold()
    severity = SEVERITY_ALIASES.get(severity_value, "medium")
    reason_codes = () if severity_value in SEVERITY_ALIASES else ("severity_defaulted",)
    environment = labels.get("environment", "unknown")
    facts = {key: value for key, value in labels.items() if key in FACT_LABELS}
    summary = alert.annotations.get("description") or title
    event_type = "alert.firing" if alert.status == "firing" else "alert.resolved"
    command_content = {
        "event_type": event_type,
        "event_at": event_at.isoformat(),
        "episode_started_at": alert.starts_at.isoformat(),
        "title": title,
        "summary": summary,
        "severity": severity,
        "service": identity.service,
        "entity_type": identity.entity_type,
        "entity_key": identity.entity_key,
        "entity_display_name": identity.display_name,
        "environment": environment,
        "facts": facts,
        "normalization_reason_codes": reason_codes,
    }
    event_identity = {
        "source_instance": source_instance,
        "fingerprint": alert.fingerprint,
        **command_content,
    }
    source_event_id = sha256(
        json.dumps(
            event_identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return SignalCommand.model_validate(
        {
            **command_content,
            "alert_source_id": alert_source_id,
            "source": "alertmanager",
            "source_instance": source_instance,
            "source_event_id": source_event_id,
            "source_alert_key": alert.fingerprint,
        }
    )
