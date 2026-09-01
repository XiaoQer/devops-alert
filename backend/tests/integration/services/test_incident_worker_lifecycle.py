from __future__ import annotations

from threading import Event

from fastapi.testclient import TestClient
from pydantic import SecretStr

from incident_intelligence.main import create_app
from incident_intelligence.services.incident_evaluation_runner import (
    IncidentEvaluationRunner,
    RunnerBatchResult,
)
from incident_intelligence.settings import Settings


def test_app_lifecycle_runs_incident_evaluation_worker(
    migrated_engine,
    monkeypatch,
) -> None:
    ran = Event()

    def record_run(self: IncidentEvaluationRunner, *, limit: int) -> RunnerBatchResult:
        del self
        assert limit == 7
        ran.set()
        return RunnerBatchResult(leased=0, succeeded=0, retried=0, failed=0)

    monkeypatch.setattr(IncidentEvaluationRunner, "run_once", record_run)
    settings = Settings(
        database_url="mysql+pymysql://tester@127.0.0.1/incident_test",
        api_token=SecretStr("api-token"),
        alertmanager_token=SecretStr("alertmanager-token"),
        cloudevents_token=SecretStr("cloudevents-token"),
        incident_workers_enabled=True,
        incident_worker_poll_seconds=1,
        incident_worker_batch_size=7,
    )

    with TestClient(create_app(settings, engine=migrated_engine)):
        assert ran.wait(timeout=2)
