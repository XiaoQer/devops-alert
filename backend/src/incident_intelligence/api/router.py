from fastapi import APIRouter
from sqlalchemy.engine import Engine

from incident_intelligence.api.routes.alertmanager import router as alertmanager_router
from incident_intelligence.api.routes.catalog import router as catalog_router
from incident_intelligence.api.routes.cloudevents import router as cloudevents_router
from incident_intelligence.api.routes.health import create_health_router
from incident_intelligence.api.routes.manual_reports import router as manual_reports_router
from incident_intelligence.api.routes.resources import create_resources_router


def create_router(engine: Engine) -> APIRouter:
    router = APIRouter()
    router.include_router(create_health_router(engine))
    router.include_router(alertmanager_router)
    router.include_router(cloudevents_router)
    router.include_router(catalog_router)
    router.include_router(manual_reports_router)
    router.include_router(create_resources_router(engine))
    return router
