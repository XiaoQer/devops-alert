from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.domain.models import Environment
from incident_intelligence.services.monitoring_data_sources import (
    ConnectionTestResult,
    MonitoringDataSourcePage,
    MonitoringDataSourceView,
    MonitoringSourceType,
)


class _SourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    environment: Environment
    source_type: MonitoringSourceType
    base_url: str = Field(min_length=1, max_length=2_000)
    credential_env_key: str | None = Field(
        default=None,
        pattern=r"^II_[A-Z0-9_]{1,120}$",
    )
    field_mapping: dict[str, str] = Field(default_factory=dict, max_length=16)
    verify_tls: bool = True
    enabled: bool


class CreateMonitoringDataSourceRequest(_SourceRequest):
    pass


class UpdateMonitoringDataSourceRequest(_SourceRequest):
    expected_version: int = Field(ge=1)


class MonitoringDataSourceResponse(MonitoringDataSourceView):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


class MonitoringDataSourcePageResponse(MonitoringDataSourcePage):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


class MonitoringConnectionTestResponse(ConnectionTestResult):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)
