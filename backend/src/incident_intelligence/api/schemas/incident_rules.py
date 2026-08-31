from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.domain.incident_rules import (
    IncidentRuleConfig,
    IncidentRuleState,
    RuleDescription,
    RuleName,
)
from incident_intelligence.services.incident_rules import (
    HistoryHours,
    IncidentRuleDeleteResult,
    IncidentRuleDryRunResult,
    IncidentRuleMutationResult,
    IncidentRulePageView,
    IncidentRuleView,
)


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateIncidentRuleRequest(_Request):
    name: RuleName
    description: RuleDescription
    config: IncidentRuleConfig


class UpdateIncidentRuleRequest(CreateIncidentRuleRequest):
    expected_version: int = Field(ge=1)


class IncidentRuleVersionRequest(_Request):
    expected_version: int = Field(ge=1)


class IncidentRuleCopyRequest(_Request):
    name: RuleName


class IncidentRuleDryRunRequest(IncidentRuleVersionRequest):
    history_hours: HistoryHours


class IncidentRuleResponse(IncidentRuleView):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


class IncidentRulePageResponse(IncidentRulePageView):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


class IncidentRuleMutationResponse(IncidentRuleMutationResult):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


class IncidentRuleDryRunResponse(IncidentRuleDryRunResult):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


class IncidentRuleDeleteResponse(IncidentRuleDeleteResult):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


__all__ = [
    "CreateIncidentRuleRequest",
    "IncidentRuleCopyRequest",
    "IncidentRuleDeleteResponse",
    "IncidentRuleDryRunRequest",
    "IncidentRuleDryRunResponse",
    "IncidentRuleMutationResponse",
    "IncidentRulePageResponse",
    "IncidentRuleResponse",
    "IncidentRuleState",
    "IncidentRuleVersionRequest",
    "UpdateIncidentRuleRequest",
]
