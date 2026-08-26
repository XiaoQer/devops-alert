from importlib import import_module

import pytest
from pydantic import ValidationError


def _load_alert_source_module():
    try:
        return import_module("incident_intelligence.domain.alert_sources")
    except ModuleNotFoundError:
        pytest.fail("告警源领域契约尚未实现")


def test_system_source_ids_are_stable_valid_source_identifiers() -> None:
    module = _load_alert_source_module()

    assert module.MANUAL_SYSTEM_SOURCE_ID == "src_00000000000000000000000000000001"
    assert module.ALERTMANAGER_COMPAT_SOURCE_ID == "src_00000000000000000000000000000002"
    assert module.CLOUDEVENTS_COMPAT_SOURCE_ID == "src_00000000000000000000000000000003"


def test_user_managed_source_rejects_manual_type() -> None:
    module = _load_alert_source_module()

    with pytest.raises(ValidationError):
        module.AlertSourceDefinition.model_validate(
            {
                "id": "src_" + "1" * 32,
                "name": "人工来源",
                "source_type": "MANUAL",
                "management_type": "USER_MANAGED",
                "state": "ENABLED",
                "version": 1,
            }
        )


def test_system_managed_source_accepts_manual_type() -> None:
    module = _load_alert_source_module()

    source = module.AlertSourceDefinition.model_validate(
        {
            "id": module.MANUAL_SYSTEM_SOURCE_ID,
            "name": "人工报告",
            "source_type": "MANUAL",
            "management_type": "SYSTEM_MANAGED",
            "state": "ENABLED",
            "version": 1,
        }
    )

    assert source.source_type == "MANUAL"
