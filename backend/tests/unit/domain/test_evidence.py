from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from incident_intelligence.domain.evidence import (
    EvidenceContext,
    EvidenceItem,
    build_evidence_window,
    summarize_run_status,
)

ANCHOR = datetime(2026, 9, 2, 6, 0, tzinfo=UTC)


def test_build_evidence_window_uses_fixed_baseline_and_caps_total_range() -> None:
    window = build_evidence_window(ANCHOR, ANCHOR + timedelta(hours=3))

    assert window.baseline_start == ANCHOR - timedelta(minutes=30)
    assert window.baseline_end == ANCHOR - timedelta(minutes=10)
    assert window.fault_start == ANCHOR - timedelta(minutes=10)
    assert window.fault_end == ANCHOR + timedelta(minutes=90)


def test_build_evidence_window_rejects_naive_times() -> None:
    with pytest.raises(ValidationError):
        build_evidence_window(ANCHOR.replace(tzinfo=None), ANCHOR)


@pytest.mark.parametrize(
    ("item_states", "expected"),
    [
        (("SUCCEEDED", "FAILED"), "PARTIAL"),
        (("NO_DATA", "SUCCEEDED"), "SUCCEEDED"),
        (("FAILED", "FAILED"), "FAILED"),
        (("MISSING_TARGET", "SKIPPED_DEPENDENCY"), "FAILED"),
    ],
)
def test_summarize_run_status_preserves_available_evidence(
    item_states: tuple[str, ...],
    expected: str,
) -> None:
    assert summarize_run_status(item_states) == expected


def test_evidence_context_enforces_service_and_alert_name_bounds() -> None:
    with pytest.raises(ValidationError):
        EvidenceContext(
            environment="testing",
            service_name="s" * 129,
            alert_names=("HighErrorRate",),
        )

    with pytest.raises(ValidationError):
        EvidenceContext(
            environment="testing",
            service_name="checkout",
            alert_names=tuple(f"Alert{index}" for index in range(201)),
        )


def test_evidence_item_rejects_non_standard_evidence_type() -> None:
    with pytest.raises(ValidationError):
        EvidenceItem.model_validate(
            {
                "id": "evitem_11111111111111111111111111111111",
                "evidence_run_id": "evr_22222222222222222222222222222222",
                "evidence_key": "common-service.service-availability",
                "display_name": "服务可用性",
                "source_type": "PROMETHEUS",
                "state": "SUCCEEDED",
                "package_id": "common-service",
                "package_version": 1,
                "template_id": "prom.service.availability",
                "template_version": 1,
                "evidence_type": "RAW_QUERY_RESULT",
                "query_started_at": ANCHOR - timedelta(minutes=30),
                "query_ended_at": ANCHOR,
                "created_at": ANCHOR,
            }
        )
