from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AlertSourceType = Literal["ALERTMANAGER", "CLOUDEVENTS", "MANUAL"]
AlertSourceManagementType = Literal["USER_MANAGED", "SYSTEM_MANAGED"]
AlertSourceState = Literal["ENABLED", "DISABLED"]
CredentialState = Literal["ACTIVE", "REVOKED"]
ReceiptOutcome = Literal[
    "ACCEPTED",
    "REPLAYED",
    "VALIDATED",
    "PAYLOAD_REJECTED",
    "SOURCE_DISABLED",
    "PROCESSING_FAILED",
]

MANUAL_SYSTEM_SOURCE_ID = "src_00000000000000000000000000000001"
ALERTMANAGER_COMPAT_SOURCE_ID = "src_00000000000000000000000000000002"
CLOUDEVENTS_COMPAT_SOURCE_ID = "src_00000000000000000000000000000003"


class AlertSourceDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^src_[0-9a-f]{32}$")
    name: str = Field(min_length=1, max_length=128)
    source_type: AlertSourceType
    management_type: AlertSourceManagementType
    state: AlertSourceState
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def reject_user_managed_manual_source(self) -> "AlertSourceDefinition":
        if self.management_type == "USER_MANAGED" and self.source_type == "MANUAL":
            raise ValueError("人工来源只能由系统管理")
        return self
