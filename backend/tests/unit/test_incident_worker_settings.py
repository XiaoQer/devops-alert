from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from incident_intelligence.settings import Settings


def test_incident_worker_settings_have_safe_bounded_defaults() -> None:
    settings = _settings()

    assert settings.incident_workers_enabled is True
    assert settings.incident_worker_poll_seconds == 2
    assert settings.incident_worker_lease_seconds == 60
    assert settings.incident_worker_max_attempts == 5
    assert settings.incident_worker_batch_size == 20


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("incident_worker_poll_seconds", 0),
        ("incident_worker_lease_seconds", 9),
        ("incident_worker_max_attempts", 11),
        ("incident_worker_batch_size", 101),
    ),
)
def test_incident_worker_settings_reject_out_of_bounds_values(
    field: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        _settings(**{field: value})


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "mysql+pymysql://tester@127.0.0.1/incident_test",
        "api_token": SecretStr("api-token"),
        "alertmanager_token": SecretStr("alertmanager-token"),
        "cloudevents_token": SecretStr("cloudevents-token"),
    }
    values.update(overrides)
    return Settings(**values)
