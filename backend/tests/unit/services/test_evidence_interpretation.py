# ruff: noqa: RUF001

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from incident_intelligence.adapters.monitoring_http import AdapterEvidenceResult
from incident_intelligence.domain.evidence import (
    EvidenceContext,
    EvidenceRun,
    build_evidence_window,
)
from incident_intelligence.services.evidence_collection import _item_from_result
from incident_intelligence.services.evidence_planning import EvidenceQueryRequest

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("query_name", "baseline", "fault", "expected"),
    (
        (
            "prom.mysql.row-lock-current-waits",
            {"maximum": 0.0},
            {"maximum": 1.0},
            "故障前当前行锁等待峰值为 0，故障期间升至 1。",
        ),
        (
            "prom.mysql.row-lock-waits-increase",
            {"maximum": 0.0},
            {"maximum": 1.058937},
            "故障前 5 分钟窗口新增行锁等待峰值为 0 次，故障期间约为 1.06 次。",
        ),
        (
            "prom.mysql.row-lock-time-increase",
            {"maximum": 0.0},
            {"maximum": 51766.37},
            "故障前 5 分钟窗口行锁等待耗时增量峰值为 0 毫秒，故障期间约为 51.8 秒。",
        ),
    ),
)
def test_mysql_metric_interpretation_states_only_observed_window_comparison(
    query_name: str,
    baseline: dict[str, float],
    fault: dict[str, float],
    expected: str,
) -> None:
    item = _item_from_result(
        _run(),
        _request(query_name),
        AdapterEvidenceResult(
            state="SUCCEEDED",
            baseline_summary=baseline,
            fault_summary=fault,
            interpretation="已获取指标。",
        ),
        lambda prefix: f"{prefix}_{'1' * 32}",
        NOW,
    )

    assert item.interpretation == expected
    assert "根因" not in item.interpretation
    assert "建议" not in item.interpretation


def test_interpretation_preserves_adapter_message_when_data_is_unavailable() -> None:
    item = _item_from_result(
        _run(),
        _request("prom.mysql.row-lock-current-waits"),
        AdapterEvidenceResult(
            state="NO_DATA",
            interpretation="查询成功。该时间窗口没有指标数据。",
        ),
        lambda prefix: f"{prefix}_{'1' * 32}",
        NOW,
    )

    assert item.interpretation == "查询成功。该时间窗口没有指标数据。"


def test_current_wait_interpretation_does_not_claim_an_increase_when_value_falls() -> None:
    item = _item_from_result(
        _run(),
        _request("prom.mysql.row-lock-current-waits"),
        AdapterEvidenceResult(
            state="SUCCEEDED",
            baseline_summary={"maximum": 2.0},
            fault_summary={"maximum": 1.0},
            interpretation="已获取指标。",
        ),
        lambda prefix: f"{prefix}_{'1' * 32}",
        NOW,
    )

    assert item.interpretation == "故障前当前行锁等待峰值为 2，故障期间降至 1。"


def _run() -> EvidenceRun:
    return EvidenceRun(
        id=f"evr_{'2' * 32}",
        incident_id=f"inc_{'3' * 32}",
        trigger="MANUAL",
        state="RUNNING",
        anchor_at=NOW,
        window=build_evidence_window(NOW, NOW),
        context=EvidenceContext(
            environment="testing",
            service_name="business-mysql",
            alert_names=("MySQLRowLockWaitActive",),
        ),
        requested_by="tester",
        created_at=NOW,
    )


def _request(query_name: str) -> EvidenceQueryRequest:
    return EvidenceQueryRequest(
        execution_key="a" * 64,
        package_id="mysql",
        package_version=2,
        template_id=query_name,
        template_version=2,
        display_name="MySQL 指标",
        source_type="PROMETHEUS",
        evidence_type="METRIC_COMPARISON",
        query_name=query_name,
        controlled_query="metric{${service_matcher}}",
        parameters={"environment": "testing", "service": "business-mysql"},
        window=build_evidence_window(NOW, NOW),
    )
