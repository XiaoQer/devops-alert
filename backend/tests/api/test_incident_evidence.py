from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.engine import Engine

from incident_intelligence.domain.evidence import (
    EvidenceContext,
    EvidenceRun,
    build_evidence_window,
)
from incident_intelligence.main import create_app
from incident_intelligence.services.incident_evidence import (
    EvidenceRunDetail,
    EvidenceRunMutationResult,
    EvidenceRunPage,
)
from incident_intelligence.settings import Settings

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
INCIDENT_ID = "inc_11111111111111111111111111111111"
RUN_ID = "evr_22222222222222222222222222222222"


def test_incident_evidence_endpoints_are_authenticated_and_bodyless_for_manual_request(
    migrated_engine: Engine,
) -> None:
    app = create_app(_settings(migrated_engine), engine=migrated_engine)
    service = _FakeEvidenceService()
    app.state.incident_evidence_service = service
    headers = {"Authorization": "Bearer api-token", "Idempotency-Key": "manual-1"}

    with TestClient(app) as client:
        created = client.post(
            f"/api/v1/incidents/{INCIDENT_ID}/evidence-runs",
            headers=headers,
        )
        listed = client.get(
            f"/api/v1/incidents/{INCIDENT_ID}/evidence-runs",
            headers={"Authorization": "Bearer api-token"},
        )
        detail = client.get(
            f"/api/v1/incidents/{INCIDENT_ID}/evidence-runs/{RUN_ID}",
            headers={"Authorization": "Bearer api-token"},
        )

    assert created.status_code == 200
    assert created.json()["run"]["id"] == RUN_ID
    assert listed.status_code == 200 and listed.json()["total"] == 1
    assert detail.status_code == 200 and detail.json()["items"] == []
    assert service.manual_calls == [(INCIDENT_ID, "manual-1", "manual-api-client")]


class _FakeEvidenceService:
    def __init__(self) -> None:
        self.manual_calls: list[tuple[str, str, str]] = []

    def list_runs(self, incident_id: str, *, limit: int, offset: int) -> EvidenceRunPage:
        assert incident_id == INCIDENT_ID
        return EvidenceRunPage(items=(_run(),), total=1, limit=limit, offset=offset)

    def get_run(self, incident_id: str, run_id: str) -> EvidenceRunDetail:
        assert (incident_id, run_id) == (INCIDENT_ID, RUN_ID)
        return EvidenceRunDetail(run=_run(), items=(), items_truncated=False)

    def request_manual(
        self,
        incident_id: str,
        *,
        idempotency_key: str,
        actor: str,
        request_id: str,
    ) -> EvidenceRunMutationResult:
        assert request_id.startswith("req_")
        self.manual_calls.append((incident_id, idempotency_key, actor))
        return EvidenceRunMutationResult(run=_run(), replayed=False)


def _run() -> EvidenceRun:
    return EvidenceRun(
        id=RUN_ID,
        incident_id=INCIDENT_ID,
        trigger="MANUAL",
        state="QUEUED",
        anchor_at=NOW,
        window=build_evidence_window(NOW, NOW),
        context=EvidenceContext(
            environment="testing",
            service_name="checkout",
            alert_names=("HighErrorRate",),
        ),
        requested_by="manual-api-client",
        created_at=NOW,
    )


def _settings(engine: Engine) -> Settings:
    return Settings(
        database_url=str(engine.url),
        api_token=SecretStr("api-token"),
        alertmanager_token=SecretStr("alertmanager-token"),
        cloudevents_token=SecretStr("cloudevents-token"),
        incident_workers_enabled=False,
    )
