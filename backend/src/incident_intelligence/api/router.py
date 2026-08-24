from fastapi import APIRouter
from sqlalchemy.engine import Engine

from incident_intelligence.api.routes.health import create_health_router
from incident_intelligence.api.routes.manual_reports import router as manual_reports_router


def create_router(engine: Engine) -> APIRouter:
    router = APIRouter()
    router.include_router(create_health_router(engine))
    router.include_router(manual_reports_router)
    return router
