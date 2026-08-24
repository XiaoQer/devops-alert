from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="II_", frozen=True, extra="ignore")

    database_url: str = Field(min_length=1)
    api_token: SecretStr
    request_body_limit_bytes: int = Field(default=65_536, gt=0, le=1_048_576)
