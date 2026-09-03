# ruff: noqa: RUF001

from __future__ import annotations

import math
from collections.abc import Mapping

from incident_intelligence.domain.evidence import EvidenceItemState


def interpret_evidence_result(
    *,
    query_name: str,
    state: EvidenceItemState,
    baseline_summary: Mapping[str, object],
    fault_summary: Mapping[str, object],
    fallback: str | None,
) -> str | None:
    if state != "SUCCEEDED":
        return fallback

    baseline_maximum = _number(baseline_summary.get("maximum"))
    fault_maximum = _number(fault_summary.get("maximum"))
    if query_name == "prom.mysql.row-lock-current-waits":
        return (
            _maximum_comparison(
                baseline_maximum,
                fault_maximum,
                prefix="当前行锁等待",
            )
            or fallback
        )
    if query_name == "prom.mysql.row-lock-waits-increase":
        if baseline_maximum is not None and fault_maximum is not None:
            return (
                "故障前 5 分钟窗口新增行锁等待峰值为 "
                f"{_format_number(baseline_maximum)} 次，故障期间约为 "
                f"{_format_number(fault_maximum)} 次。"
            )
        return fallback
    if query_name == "prom.mysql.row-lock-time-increase":
        if baseline_maximum is not None and fault_maximum is not None:
            return (
                "故障前 5 分钟窗口行锁等待耗时增量峰值为 "
                f"{_format_duration_ms(baseline_maximum)}，故障期间约为 "
                f"{_format_duration_ms(fault_maximum)}。"
            )
        return fallback

    baseline_average = _number(baseline_summary.get("average"))
    fault_average = _number(fault_summary.get("average"))
    if baseline_average is None or fault_average is None:
        return fallback
    if query_name == "prom.service.availability":
        delta = fault_average - baseline_average
        if math.isclose(delta, 0.0, abs_tol=1e-9):
            change = "未见变化"
        elif delta < 0:
            change = f"下降了 {_format_number(abs(delta))}"
        else:
            change = f"上升了 {_format_number(delta)}"
        return (
            f"故障前服务可用性平均值为 {_format_number(baseline_average)}，"
            f"故障期间为 {_format_number(fault_average)}，{change}。"
        )
    return (
        f"故障前平均值为 {_format_number(baseline_average)}，"
        f"故障期间为 {_format_number(fault_average)}。"
    )


def _maximum_comparison(
    baseline: float | None,
    fault: float | None,
    *,
    prefix: str,
) -> str | None:
    if baseline is None or fault is None:
        return None
    if math.isclose(fault, baseline, abs_tol=1e-9):
        fault_text = f"保持 {_format_number(fault)}"
    elif fault > baseline:
        fault_text = f"升至 {_format_number(fault)}"
    else:
        fault_text = f"降至 {_format_number(fault)}"
    return f"故障前{prefix}峰值为 {_format_number(baseline)}，故障期间{fault_text}。"


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    normalized = float(value)
    return normalized if math.isfinite(normalized) else None


def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _format_duration_ms(value: float) -> str:
    if abs(value) < 1_000:
        return f"{_format_number(value)} 毫秒"
    return f"{value / 1_000:.1f} 秒"
