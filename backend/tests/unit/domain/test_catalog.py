from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from incident_intelligence.domain.catalog import (
    ServiceCatalogEntry,
    ServiceDependency,
    normalize_symptom,
)

NOW = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("high-error-rate", "error_rate"),
        ("errors", "error_rate"),
        ("latency", "latency"),
        ("slow", "latency"),
        ("cpu", "cpu_saturation"),
        ("high-cpu", "cpu_saturation"),
        ("oom", "memory_pressure"),
        ("memory", "memory_pressure"),
        ("down", "availability"),
        ("unavailable", "availability"),
        (" CPU ", "cpu_saturation"),
        ("private-new-symptom", None),
        ("", None),
        (None, None),
    ],
)
def test_symptom_normalization_uses_only_fixed_aliases(
    raw: str | None,
    expected: str | None,
) -> None:
    assert normalize_symptom(raw) == expected


def test_catalog_entry_is_strict_bounded_and_frozen() -> None:
    entry = ServiceCatalogEntry(
        id="svc_" + "1" * 32,
        service=" payment-api ",
        environment="production",
        owner_team=" payments ",
        state="ACTIVE",
        created_at=NOW,
        updated_at=NOW,
    )

    assert entry.service == "payment-api"
    assert entry.owner_team == "payments"
    assert entry.version == 1
    with pytest.raises(ValidationError):
        entry.owner_team = "platform"

    for invalid in (
        {"id": "service-1"},
        {"owner_team": "x" * 129},
        {"environment": "Private environment"},
        {"state": "DELETED"},
        {"version": 0},
        {"unexpected": "value"},
    ):
        values = {
            "id": "svc_" + "1" * 32,
            "service": "payment-api",
            "environment": "production",
            "owner_team": "payments",
            "state": "ACTIVE",
            "created_at": NOW,
            "updated_at": NOW,
            **invalid,
        }
        with pytest.raises(ValidationError):
            ServiceCatalogEntry.model_validate(values)


def test_dependency_rejects_invalid_ids_state_and_extra_fields() -> None:
    dependency = ServiceDependency(
        id="dep_" + "2" * 32,
        caller_service_id="svc_" + "1" * 32,
        dependency_service_id="svc_" + "2" * 32,
        state="ACTIVE",
        created_at=NOW,
        updated_at=NOW,
    )
    assert dependency.version == 1

    for invalid in (
        {"id": "dependency-1"},
        {"caller_service_id": "svc_invalid"},
        {"dependency_service_id": "svc_invalid"},
        {"state": "DELETED"},
        {"unexpected": "value"},
    ):
        values = {
            "id": "dep_" + "2" * 32,
            "caller_service_id": "svc_" + "1" * 32,
            "dependency_service_id": "svc_" + "2" * 32,
            "state": "ACTIVE",
            "created_at": NOW,
            "updated_at": NOW,
            **invalid,
        }
        with pytest.raises(ValidationError):
            ServiceDependency.model_validate(values)
