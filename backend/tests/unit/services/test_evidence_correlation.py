from __future__ import annotations

from datetime import UTC, datetime, timedelta

from incident_intelligence.domain.evidence import EvidenceContext, EvidenceItem
from incident_intelligence.services.evidence_correlation import EvidenceCorrelationService

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def test_correlates_trace_logs_only_when_trace_id_and_scope_match() -> None:
    service = EvidenceCorrelationService()
    context = EvidenceContext(
        environment="testing",
        service_name="checkout",
        alert_names=("HighHttpErrorRate",),
    )

    items = service.correlate(
        context,
        (_trace_item("trace-1"), _log_item("trace-1")),
    )

    assert len(items) == 1
    assert items[0].normalized_result["matched_trace_count"] == 1
    assert items[0].interpretation == "失败 Trace 在同服务、同时间窗口找到对应日志"
    assert items[0].state == "SUCCEEDED"
    assert items[0].source_type == "PLATFORM"


def test_does_not_correlate_different_trace_ids() -> None:
    items = EvidenceCorrelationService().correlate(
        EvidenceContext(
            environment="testing",
            service_name="checkout",
            alert_names=("HighHttpErrorRate",),
        ),
        (_trace_item("trace-1"), _log_item("trace-2")),
    )

    assert items == ()


def test_missing_skywalking_marks_dependency_skipped_not_normal() -> None:
    failed_trace = _trace_item("trace-1").model_copy(
        update={"state": "FAILED", "normalized_result": {}, "error_code": "timeout"}
    )

    item = EvidenceCorrelationService().correlate(
        EvidenceContext(
            environment="testing",
            service_name="checkout",
            alert_names=("HighHttpErrorRate",),
        ),
        (failed_trace, _log_item("trace-1")),
    )[0]

    assert item.state == "SKIPPED_DEPENDENCY"
    assert "正常" not in item.interpretation
    assert "根因" not in item.interpretation


def _trace_item(trace_id: str) -> EvidenceItem:
    return _item(
        item_id="evitem_11111111111111111111111111111111",
        evidence_key="http.failed-traces",
        source_type="SKYWALKING",
        evidence_type="TRACE_SUMMARY",
        normalized_result={
            "traces": [
                {
                    "trace_id": trace_id,
                    "endpoint": "/checkout",
                    "duration_ms": 1200,
                    "error": True,
                    "spans": [],
                }
            ]
        },
    )


def _log_item(trace_id: str) -> EvidenceItem:
    return _item(
        item_id="evitem_22222222222222222222222222222222",
        evidence_key="common-service.errors",
        source_type="ELASTICSEARCH",
        evidence_type="LOG_SAMPLE",
        normalized_result={
            "samples": [
                {
                    "trace_id": trace_id,
                    "message": "request failed",
                    "fingerprint": "a" * 64,
                }
            ]
        },
    )


def _item(
    *,
    item_id: str,
    evidence_key: str,
    source_type: str,
    evidence_type: str,
    normalized_result: dict[str, object],
) -> EvidenceItem:
    return EvidenceItem.model_validate(
        {
            "id": item_id,
            "evidence_run_id": "evr_33333333333333333333333333333333",
            "evidence_key": evidence_key,
            "display_name": "测试证据",
            "source_type": source_type,
            "state": "SUCCEEDED",
            "package_id": "common-service",
            "package_version": 1,
            "template_id": evidence_key,
            "template_version": 1,
            "evidence_type": evidence_type,
            "query_started_at": NOW - timedelta(minutes=30),
            "query_ended_at": NOW,
            "normalized_result": normalized_result,
            "created_at": NOW,
        }
    )
