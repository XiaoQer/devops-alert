from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from incident_intelligence.domain.correlation import (
    CorrelationContext,
    CorrelationDecisionDraft,
    decide_correlation,
)
from incident_intelligence.domain.enums import AlertGroupState, CatalogState
from incident_intelligence.domain.models import Environment, Severity

IncidentId = Annotated[str, StringConstraints(pattern=r"^inc_[0-9a-f]{32}$")]


class AlertGroupCorrelationContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    target_group_version: int = Field(ge=1)
    current_group_version: int = Field(ge=1)
    group_state: AlertGroupState
    severity: Severity
    environment: Environment
    catalog_state: CatalogState | None
    existing_incident_id: IncidentId | None = None
    exact_candidate_ids: tuple[IncidentId, ...] = Field(default=(), max_length=21)
    dependency_candidate_ids: tuple[IncidentId, ...] = Field(default=(), max_length=21)


def decide_alert_group_correlation(
    context: AlertGroupCorrelationContext,
) -> CorrelationDecisionDraft:
    return decide_correlation(
        CorrelationContext.model_validate(
            {
                "alert_version": context.target_group_version,
                "current_alert_version": context.current_group_version,
                "alert_state": (
                    "ACTIVE" if context.group_state is AlertGroupState.ACTIVE else "RESOLVED"
                ),
                "severity": context.severity,
                "environment": context.environment,
                "catalog_state": context.catalog_state,
                "existing_incident_id": context.existing_incident_id,
                "exact_candidate_ids": context.exact_candidate_ids,
                "dependency_candidate_ids": context.dependency_candidate_ids,
            }
        )
    )
