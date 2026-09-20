from __future__ import annotations

from incident_intelligence.adapters.demo_dify import DemoDifyWorkflow
from incident_intelligence.services.diagnosis_tools import (
    DiagnosisEvidenceDetailToolResult,
    DiagnosisSnapshotEvidenceSummary,
    DiagnosisSnapshotToolResult,
)

RUN_ID = f"drun_{'1' * 32}"
EVIDENCE_ID = f"evitem_{'2' * 32}"


def test_demo_workflow_reads_snapshot_then_evidence_before_returning_draft() -> None:
    tools = _FakeDiagnosisTools()

    draft = DemoDifyWorkflow(tools).run_workflow(RUN_ID, capability_token="not-persisted")

    assert tools.calls == [("snapshot", RUN_ID)]
    assert draft["confirmed_facts"][0]["reference_ids"] == ["E1"]
    assert draft["references"] == ["E1"]


class _FakeDiagnosisTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_diagnosis_snapshot(
        self,
        diagnosis_run_id: str,
        *,
        capability_token: str,
    ) -> DiagnosisSnapshotToolResult:
        del capability_token
        self.calls.append(("snapshot", diagnosis_run_id))
        return DiagnosisSnapshotToolResult(
            diagnosis_run_id=diagnosis_run_id,
            incident_id=f"inc_{'3' * 32}",
            environment="testing",
            service_name="checkout",
            alert_names=("HighErrorRate",),
            alert_facts=(),
            evidence_summaries=(
                DiagnosisSnapshotEvidenceSummary(
                    alias="E1",
                    display_name="数据库行锁等待",
                    source_type="PROMETHEUS",
                    state="SUCCEEDED",
                    evidence_type="METRIC_COMPARISON",
                    baseline_summary={"current": 0},
                    fault_summary={"current": 1},
                    interpretation="数据库行锁等待升高。",
                ),
            ),
            truncated=False,
        )

    def get_evidence_detail(
        self,
        diagnosis_run_id: str,
        *,
        capability_token: str,
        evidence_item_id: str,
    ) -> DiagnosisEvidenceDetailToolResult:
        del diagnosis_run_id, capability_token
        self.calls.append(("evidence", evidence_item_id))
        return DiagnosisEvidenceDetailToolResult(
            id=evidence_item_id,
            display_name="数据库行锁等待",
            source_type="PROMETHEUS",
            state="SUCCEEDED",
            evidence_type="METRIC_COMPARISON",
            baseline_summary={"current": 0},
            fault_summary={"current": 1},
            interpretation="数据库行锁等待升高。",
            truncated=False,
        )
