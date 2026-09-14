# ruff: noqa: RUF001

from __future__ import annotations

from datetime import UTC, datetime

from incident_intelligence.domain.diagnosis import (
    DiagnosisReference,
    DiagnosisSnapshot,
    validate_candidate_report,
)

NOW = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
EVIDENCE_ID = f"evitem_{'1' * 32}"
KNOWLEDGE_ID = f"kbchunk_{'2' * 32}"


def test_report_rejects_confirmed_fact_without_matching_evidence_reference() -> None:
    result = validate_candidate_report(
        _snapshot(),
        {
            "confirmed_facts": [{"text": "数据库行锁等待明显升高。", "reference_ids": []}],
            "hypotheses": [],
            "references": [],
            "unknowns": [],
            "suggested_human_actions": [],
        },
    )

    assert result.accepted is False
    assert result.reason_code == "diagnosis_fact_reference_missing"


def test_report_rejects_reference_that_is_not_part_of_snapshot_or_tool_receipt() -> None:
    result = validate_candidate_report(
        _snapshot(),
        {
            "confirmed_facts": [
                {"text": "数据库行锁等待升高。", "reference_ids": ["evitem_missing"]}
            ],
            "hypotheses": [],
            "references": [
                {
                    "kind": "EVIDENCE",
                    "target_id": "evitem_missing",
                    "content_hash": "a" * 64,
                }
            ],
            "unknowns": [],
            "suggested_human_actions": [],
        },
    )

    assert result.accepted is False
    assert result.reason_code == "diagnosis_reference_not_allowed"


def test_report_accepts_hypothesis_only_when_marked_for_verification_and_cited() -> None:
    result = validate_candidate_report(
        _snapshot(),
        {
            "confirmed_facts": [
                {"text": "故障期间当前行锁等待从 0 升至 1。", "reference_ids": [EVIDENCE_ID]}
            ],
            "hypotheses": [
                {
                    "text": "待验证：行锁等待可能与当前请求积压有关。",
                    "verification_required": True,
                    "reference_ids": [EVIDENCE_ID, KNOWLEDGE_ID],
                }
            ],
            "references": [
                {
                    "kind": "EVIDENCE",
                    "target_id": EVIDENCE_ID,
                    "content_hash": "1" * 64,
                },
                {
                    "kind": "KNOWLEDGE",
                    "target_id": KNOWLEDGE_ID,
                    "content_hash": "2" * 64,
                },
            ],
            "unknowns": ["尚未取得数据库慢查询日志。"],
            "suggested_human_actions": ["请人工检查当前数据库会话和慢查询记录。"],
        },
    )

    assert result.accepted is True
    assert result.report is not None
    assert result.report.hypotheses[0].verification_required is True


def test_report_rejects_suggested_action_that_contains_an_executable_command() -> None:
    result = validate_candidate_report(
        _snapshot(),
        {
            "confirmed_facts": [],
            "hypotheses": [],
            "references": [],
            "unknowns": [],
            "suggested_human_actions": ["执行 kubectl rollout restart deployment/mysql。"],
        },
    )

    assert result.accepted is False
    assert result.reason_code == "diagnosis_action_not_human_only"


def _snapshot() -> DiagnosisSnapshot:
    return DiagnosisSnapshot(
        diagnosis_run_id=f"drun_{'3' * 32}",
        incident_id=f"inc_{'4' * 32}",
        evidence_run_id=f"evr_{'5' * 32}",
        environment="staging",
        service_name="business-mysql",
        alert_names=("MySQLRowLockWaitActive",),
        evidence_references=(
            DiagnosisReference(
                kind="EVIDENCE",
                target_id=EVIDENCE_ID,
                content_hash="1" * 64,
            ),
        ),
        knowledge_references=(
            DiagnosisReference(
                kind="KNOWLEDGE",
                target_id=KNOWLEDGE_ID,
                content_hash="2" * 64,
            ),
        ),
        created_at=NOW,
    )
