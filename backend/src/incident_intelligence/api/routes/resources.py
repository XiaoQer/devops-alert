from __future__ import annotations

from re import Pattern
from re import compile as compile_pattern

from fastapi import APIRouter, Depends
from sqlalchemy.engine import Engine

from incident_intelligence.api.dependencies import require_manual_actor
from incident_intelligence.api.errors import ApiError
from incident_intelligence.api.schemas.resources import (
    AlertResponse,
    DiagnosisRunResponse,
    IncidentResponse,
    SignalEventResponse,
)
from incident_intelligence.persistence.repositories import RecordRepositories
from incident_intelligence.persistence.session import make_session_factory

SIGNAL_ID = compile_pattern(r"^sig_[0-9a-f]{32}$")
ALERT_ID = compile_pattern(r"^alt_[0-9a-f]{32}$")
INCIDENT_ID = compile_pattern(r"^inc_[0-9a-f]{32}$")
DIAGNOSIS_ID = compile_pattern(r"^diag_[0-9a-f]{32}$")


def create_resources_router(engine: Engine) -> APIRouter:
    session_factory = make_session_factory(engine)
    router = APIRouter(
        prefix="/api/v1",
        tags=["resources"],
        dependencies=[Depends(require_manual_actor)],
    )

    @router.get("/signals/{signal_event_id}", response_model=SignalEventResponse)
    def get_signal(signal_event_id: str) -> SignalEventResponse:
        _require_valid_id(signal_event_id, SIGNAL_ID)
        with session_factory() as session:
            row = RecordRepositories(session).find_signal(signal_event_id)
            if row is None:
                raise _not_found()
            return SignalEventResponse.model_validate(row)

    @router.get("/alerts/{alert_id}", response_model=AlertResponse)
    def get_alert(alert_id: str) -> AlertResponse:
        _require_valid_id(alert_id, ALERT_ID)
        with session_factory() as session:
            row = RecordRepositories(session).find_alert(alert_id)
            if row is None:
                raise _not_found()
            return AlertResponse.model_validate(row)

    @router.get("/incidents/{incident_id}", response_model=IncidentResponse)
    def get_incident(incident_id: str) -> IncidentResponse:
        _require_valid_id(incident_id, INCIDENT_ID)
        with session_factory() as session:
            row = RecordRepositories(session).find_incident(incident_id)
            if row is None:
                raise _not_found()
            return IncidentResponse.model_validate(row)

    @router.get("/diagnosis-runs/{diagnosis_run_id}", response_model=DiagnosisRunResponse)
    def get_diagnosis_run(diagnosis_run_id: str) -> DiagnosisRunResponse:
        _require_valid_id(diagnosis_run_id, DIAGNOSIS_ID)
        with session_factory() as session:
            row = RecordRepositories(session).find_diagnosis(diagnosis_run_id)
            if row is None:
                raise _not_found()
            return DiagnosisRunResponse.model_validate(row)

    return router


def _require_valid_id(resource_id: str, pattern: Pattern[str]) -> None:
    if pattern.fullmatch(resource_id) is None:
        raise _not_found()


def _not_found() -> ApiError:
    return ApiError(404, "resource_not_found", "未找到指定资源")
