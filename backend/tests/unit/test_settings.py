import pytest
from pydantic import SecretStr, ValidationError

from incident_intelligence.settings import Settings


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "mysql+pymysql://test@127.0.0.1/incident_intelligence",
        "api_token": SecretStr("a" * 32),
        "alertmanager_token": SecretStr("b" * 32),
        "cloudevents_token": SecretStr("c" * 32),
    }
    values.update(overrides)
    return Settings(**values)


def test_correlation_runner_settings_have_safe_bounded_defaults() -> None:
    configured = settings()

    assert configured.correlation_runner_enabled is True
    assert configured.correlation_poll_interval_seconds == 1.0
    assert configured.correlation_lease_seconds == 30
    assert configured.correlation_batch_size == 10
    assert configured.alert_group_backfill_batch_size == 100

    for overrides in (
        {"correlation_poll_interval_seconds": 0.09},
        {"correlation_poll_interval_seconds": 61},
        {"correlation_lease_seconds": 4},
        {"correlation_lease_seconds": 301},
        {"correlation_batch_size": 0},
        {"correlation_batch_size": 51},
        {"alert_group_backfill_batch_size": 0},
        {"alert_group_backfill_batch_size": 101},
    ):
        with pytest.raises(ValidationError):
            settings(**overrides)
