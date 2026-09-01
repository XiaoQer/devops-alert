from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def validate_mysql_database_url(value: str) -> str:
    try:
        url = make_url(value)
    except ArgumentError as error:
        raise ValueError("database_url 必须是包含数据库名的 mysql+pymysql URL") from error
    if url.drivername != "mysql+pymysql" or not url.database:
        raise ValueError("database_url 必须是包含数据库名的 mysql+pymysql URL")
    return value


class FeishuCapabilityView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    configured: bool
    missing_environment_keys: tuple[str, ...]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="II_", frozen=True, extra="ignore")

    database_url: str = Field(min_length=1)
    api_token: SecretStr
    alertmanager_token: SecretStr
    cloudevents_token: SecretStr
    feishu_app_id: SecretStr | None = None
    feishu_app_secret: SecretStr | None = None
    feishu_verification_token: SecretStr | None = None
    feishu_encrypt_key: SecretStr | None = None
    request_body_limit_bytes: int = Field(default=65_536, gt=0, le=1_048_576)
    alertmanager_body_limit_bytes: int = Field(default=262_144, ge=65_536, le=1_048_576)
    cloudevents_body_limit_bytes: int = Field(default=65_536, gt=0, le=262_144)
    incident_workers_enabled: bool = True
    incident_worker_poll_seconds: int = Field(default=2, ge=1, le=60)
    incident_worker_lease_seconds: int = Field(default=60, ge=10, le=300)
    incident_worker_max_attempts: int = Field(default=5, ge=1, le=10)
    incident_worker_batch_size: int = Field(default=20, ge=1, le=100)
    correlation_runner_enabled: bool = True
    correlation_poll_interval_seconds: float = Field(default=1.0, ge=0.1, le=60)
    correlation_lease_seconds: int = Field(default=30, ge=5, le=300)
    correlation_batch_size: int = Field(default=10, ge=1, le=50)
    alert_group_backfill_batch_size: int = Field(default=100, ge=1, le=100)
    alert_grouping_runner_enabled: bool = True
    alert_grouping_poll_interval_seconds: float = Field(default=1.0, ge=0.1, le=60)
    alert_grouping_lease_seconds: int = Field(default=30, ge=5, le=300)
    alert_grouping_batch_size: int = Field(default=10, ge=1, le=50)
    alert_event_lifecycle_runner_enabled: bool = True
    alert_event_lifecycle_poll_interval_seconds: float = Field(default=1.0, ge=0.1, le=60)
    alert_event_lifecycle_lease_seconds: int = Field(default=30, ge=5, le=300)
    alert_event_lifecycle_batch_size: int = Field(default=10, ge=1, le=50)

    @field_validator("database_url")
    @classmethod
    def require_mysql_database_url(cls, value: str) -> str:
        return validate_mysql_database_url(value)

    def feishu_capability(self) -> FeishuCapabilityView:
        required = (
            ("II_FEISHU_APP_ID", self.feishu_app_id),
            ("II_FEISHU_APP_SECRET", self.feishu_app_secret),
            ("II_FEISHU_VERIFICATION_TOKEN", self.feishu_verification_token),
        )
        missing = tuple(name for name, value in required if not _has_secret(value))
        return FeishuCapabilityView(
            configured=not missing,
            missing_environment_keys=missing,
        )


def _has_secret(value: SecretStr | None) -> bool:
    return value is not None and bool(value.get_secret_value().strip())
