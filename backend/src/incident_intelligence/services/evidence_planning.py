from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.domain.evidence import (
    EvidenceContext,
    EvidenceType,
    EvidenceWindow,
    MonitoringSourceType,
)
from incident_intelligence.domain.evidence_packs import EvidencePackRegistry
from incident_intelligence.domain.models import UtcAwareDatetime


class IncidentAlertEvidenceFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    episode_started_at: UtcAwareDatetime | None
    first_received_at: UtcAwareDatetime


class EvidenceQueryRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    package_id: str
    package_version: int = Field(ge=1)
    template_id: str
    template_version: int = Field(ge=1)
    display_name: str
    source_type: MonitoringSourceType
    evidence_type: EvidenceType
    query_name: str
    controlled_query: str
    parameters: dict[str, str]
    window: EvidenceWindow
    pre_result_state: Literal["MISSING_TARGET"] | None = None


class EvidenceExecutionPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pack_versions: tuple[str, ...] = Field(max_length=5)
    items: tuple[EvidenceQueryRequest, ...] = Field(max_length=40)


class EvidencePlanningService:
    def __init__(self, registry: EvidencePackRegistry) -> None:
        self._registry = registry

    def plan(
        self,
        context: EvidenceContext,
        window: EvidenceWindow,
    ) -> EvidenceExecutionPlan:
        packs = self._registry.resolve(context.alert_names, context.facts)
        items: list[EvidenceQueryRequest] = []
        seen: set[str] = set()
        for pack in packs:
            for template in pack.templates:
                parameters = {"environment": context.environment}
                pre_result_state: Literal["MISSING_TARGET"] | None = None
                if "service" in template.parameters:
                    if context.service_name is None:
                        pre_result_state = "MISSING_TARGET"
                    else:
                        parameters["service"] = context.service_name
                execution_key = _execution_key(
                    template.source_type,
                    template.query_name,
                    parameters,
                    window,
                )
                if execution_key in seen:
                    continue
                seen.add(execution_key)
                items.append(
                    EvidenceQueryRequest(
                        execution_key=execution_key,
                        package_id=pack.id,
                        package_version=pack.version,
                        template_id=template.id,
                        template_version=template.version,
                        display_name=template.display_name,
                        source_type=template.source_type,
                        evidence_type=template.evidence_type,
                        query_name=template.query_name,
                        controlled_query=template.controlled_query,
                        parameters=parameters,
                        window=window,
                        pre_result_state=pre_result_state,
                    )
                )
        return EvidenceExecutionPlan(
            pack_versions=tuple(f"{pack.id}:v{pack.version}" for pack in packs),
            items=tuple(items[:40]),
        )


def choose_anchor(
    alerts: tuple[IncidentAlertEvidenceFact, ...],
) -> tuple[datetime, str]:
    if not alerts:
        raise ValueError("incident_alert_evidence_required")
    started = tuple(
        item.episode_started_at for item in alerts if item.episode_started_at is not None
    )
    if started:
        return min(started), "episode_started_at"
    return min(item.first_received_at for item in alerts), "first_received_at_fallback"


def _execution_key(
    source_type: MonitoringSourceType,
    query_name: str,
    parameters: dict[str, str],
    window: EvidenceWindow,
) -> str:
    canonical = json.dumps(
        {
            "source_type": source_type,
            "query_name": query_name,
            "parameters": parameters,
            "window": window.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode()).hexdigest()
