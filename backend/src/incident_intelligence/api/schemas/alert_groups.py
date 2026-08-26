from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.services.alert_group_center import (
    AlertGroupMemberPage,
    AlertGroupOverview,
    AlertGroupPage,
    AlertGroupSummary,
)
from incident_intelligence.services.alert_regrouping import AlertRegroupResult


class AlertRegroupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=100, ge=1, le=100)


class AlertRegroupResponse(AlertRegroupResult):
    pass


class AlertGroupPageResponse(AlertGroupPage):
    pass


class AlertGroupSummaryResponse(AlertGroupSummary):
    pass


class AlertGroupOverviewResponse(AlertGroupOverview):
    pass


class AlertGroupMemberPageResponse(AlertGroupMemberPage):
    pass
