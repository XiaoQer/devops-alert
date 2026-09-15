from __future__ import annotations

from typing import Protocol

from incident_intelligence.services.diagnosis_tools import (
    DiagnosisEvidenceDetailToolResult,
    DiagnosisSnapshotToolResult,
)


class DiagnosisToolClient(Protocol):
    def get_diagnosis_snapshot(
        self,
        diagnosis_run_id: str,
        *,
        capability_token: str,
    ) -> DiagnosisSnapshotToolResult: ...

    def get_evidence_detail(
        self,
        diagnosis_run_id: str,
        *,
        capability_token: str,
        evidence_item_id: str,
    ) -> DiagnosisEvidenceDetailToolResult: ...


class DemoDifyWorkflow:
    """Fixed local demonstration workflow; not a Dify or model invocation."""

    def __init__(self, tools: DiagnosisToolClient) -> None:
        self._tools = tools

    def run_workflow(self, diagnosis_run_id: str, *, capability_token: str) -> dict[str, object]:
        snapshot = self._tools.get_diagnosis_snapshot(
            diagnosis_run_id,
            capability_token=capability_token,
        )
        evidence_references = tuple(
            reference for reference in snapshot.evidence_references if reference.kind == "EVIDENCE"
        )
        if not evidence_references:
            return {
                "confirmed_facts": [],
                "hypotheses": [],
                "references": [],
                "unknowns": ["当前诊断快照没有可用监控证据。"],
                "suggested_human_actions": ["请人工补充取证后重新发起分析。"],
            }

        reference = evidence_references[0]
        evidence = self._tools.get_evidence_detail(
            diagnosis_run_id,
            capability_token=capability_token,
            evidence_item_id=reference.target_id,
        )
        fact = evidence.interpretation or f"{evidence.display_name} 的状态为 {evidence.state}。"
        return {
            "confirmed_facts": [{"text": fact, "reference_ids": [reference.target_id]}],
            "hypotheses": [],
            "references": [reference.model_dump(mode="json")],
            "unknowns": ["本地演示执行器不会生成根因结论。"],
            "suggested_human_actions": ["请人工结合应用日志和变更记录继续核对。"],
        }
