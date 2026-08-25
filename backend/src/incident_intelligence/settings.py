from pydantic import Field, SecretStr, field_validator
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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="II_", frozen=True, extra="ignore")

    database_url: str = Field(min_length=1)
    api_token: SecretStr
    request_body_limit_bytes: int = Field(default=65_536, gt=0, le=1_048_576)

    @field_validator("database_url")
    @classmethod
    def require_mysql_database_url(cls, value: str) -> str:
        return validate_mysql_database_url(value)
