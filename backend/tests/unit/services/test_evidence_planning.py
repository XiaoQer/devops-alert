from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from incident_intelligence.domain.evidence import EvidenceContext, build_evidence_window
from incident_intelligence.domain.evidence_packs import EvidencePackRegistry
from incident_intelligence.services.evidence_planning import (
    EvidencePlanningService,
    IncidentAlertEvidenceFact,
    choose_anchor,
)

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def test_plan_selects_common_http_and_mysql_without_duplicate_queries() -> None:
    planner = EvidencePlanningService(EvidencePackRegistry.default())
    context = EvidenceContext(
        environment="testing",
        service_name="checkout",
        alert_names=("HighHttpErrorRate", "MySQLRowLockWaitActive"),
        facts={"component": "mysql"},
    )

    plan = planner.plan(context, build_evidence_window(NOW, NOW))

    assert plan.pack_versions == (
        "common-service:v2",
        "http:v2",
        "mysql:v2",
    )
    assert len({item.execution_key for item in plan.items}) == len(plan.items)
    assert all(item.parameters["service"] == "checkout" for item in plan.items)


def test_plan_marks_service_queries_missing_without_inventing_target() -> None:
    planner = EvidencePlanningService(EvidencePackRegistry.default())
    context = EvidenceContext(
        environment="testing",
        service_name=None,
        alert_names=("HighHttpErrorRate",),
    )

    plan = planner.plan(context, build_evidence_window(NOW, NOW))

    assert plan.items
    assert {item.pre_result_state for item in plan.items} == {"MISSING_TARGET"}
    assert all("service" not in item.parameters for item in plan.items)


@pytest.mark.parametrize("key", ["scenario_id", "scenario_version", "experiment_id"])
def test_context_rejects_forbidden_fault_injection_identity(key: str) -> None:
    with pytest.raises(ValidationError):
        EvidenceContext(
            environment="testing",
            service_name="checkout",
            alert_names=("HighHttpErrorRate",),
            facts={key: "forbidden"},
        )


def test_choose_anchor_prefers_earliest_episode_and_falls_back_to_receive_time() -> None:
    episode = IncidentAlertEvidenceFact(
        episode_started_at=NOW - timedelta(minutes=4),
        first_received_at=NOW - timedelta(minutes=3),
    )
    fallback = IncidentAlertEvidenceFact(
        episode_started_at=None,
        first_received_at=NOW - timedelta(minutes=5),
    )

    assert choose_anchor((episode, fallback)) == (
        NOW - timedelta(minutes=4),
        "episode_started_at",
    )
    assert choose_anchor((fallback,)) == (
        NOW - timedelta(minutes=5),
        "first_received_at_fallback",
    )
