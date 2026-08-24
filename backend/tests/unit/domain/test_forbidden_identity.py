from __future__ import annotations

import pytest

from incident_intelligence.domain.forbidden_identity import (
    ForbiddenIdentityError,
    reject_forbidden_identity,
)


@pytest.mark.parametrize(
    ("payload", "expected_key"),
    [
        ({"scenario_id": "s1"}, "scenario_id"),
        ({"labels": {"Scenario-Version": "v1"}}, "Scenario-Version"),
        ({"context": [{"experimentId": "e1"}]}, "experimentId"),
        ({"metadata": {"injection action": "cpu"}}, "injection action"),
        ({"facts": {"ground.truth": "answer"}}, "ground.truth"),
        ({"上下文": {"注入动作": "cpu"}}, "注入动作"),
        ({"事实": {"标准答案": "answer"}}, "标准答案"),
    ],
)
def test_experiment_identity_is_rejected_at_any_depth(payload: object, expected_key: str) -> None:
    with pytest.raises(ForbiddenIdentityError) as error:
        reject_forbidden_identity(payload)

    assert error.value.reason_code == "forbidden_identity"
    assert error.value.key == expected_key
    assert "s1" not in str(error.value)
    assert "answer" not in str(error.value)


def test_normal_operational_facts_are_accepted() -> None:
    reject_forbidden_identity(
        {
            "service": "payment-api",
            "environment": "production",
            "labels": {"region": "cn-east-1", "symptom": "high-error-rate"},
        }
    )
